#!/usr/bin/python

import os
import requests
import re
import gzip
import shutil
from bzrlib.branch import Branch
import ConfigParser

# NOTE: it is up to you to set up apt correctly.  If you're
# using this script, you probably want something like this
# in your apt configs:
#
# deb http://openstack-repo.cisco.com/openstack/cisco folsom-proposed main
# deb-src http://openstack-repo.cisco.com/openstack/cisco folsom-proposed main
#

# Instantiate a ConfigParser object.
config = ConfigParser.RawConfigParser()
config.add_section('check_integrity')

#####################################################
# CONFIGURABLES
#
# These are defaults and will get overridden by settings in
# check_integrity.cfg.
#
# TODO(mvoelker): make these CLI args.
#
# The series you want to validate.  This should be the series
# number as assigned by repomgmt.
config.set('check_integrity', 'series_number', '1')

# To interact with python-django-repomgmt, we'll need to
# authenticate our API calls.  Set your username and API key
# here.  Your username is most likely your CEC username; your
# API key can be found by loging in to python-django-repomgmt
# and clicking on "Logged in as [your userid]" in the upper right
# corner.
config.set('check_integrity', 'repomgmt_api_username', 'someuserid')
config.set('check_integrity', 'repomgmt_api_key',
    '1111111111111111111111111111111111111111')

# We'll also need credentials for GitHub in order to avoid the
# very low (60/hour as of this writing) rate limits for
# unauthenticated API calls.  By making our API calls authenticated,
# we get a bump to 5000 calls/hour.
config.set('check_integrity', 'github_username', 'someuserid')
config.set('check_integrity', 'github_pass', 'somepassword')

# Temporary directories used to extract packages and
# clone bzr branches.  The defaults are probably fine here,
# but if you're extremely space constrained you may want
# to change them.
config.set('check_integrity', 'bzr_tmp', '/tmp/code/')
config.set('check_integrity', 'pkg_tmp', '/tmp/package_extract')

# The URL of the python-django-repomgmt instance we'll be talking to.
# This should be the "root" page...e.g. what you'd type into a browser
# to visit the tool.
config.set('check_integrity', 'baseurl', 'http://apt.ctocllab.cisco.com/')

# End of configurables.


def read_config(filepath):
    """
    Reads a config file (passed in as a string) if the specified
    file exists.  Sets the expected values into global variables
    for use elsewhere in the script.  If the value passed in
    is an empty string, looks for ./check_integrity.cfg
    by default.
    """
    if filepath == '':
        # Default to looking in cwd for check_integrity.cfg.
        filepath = './check_integrity.cfg'

    # Read in a config (if one exists) to override the defaults above.
    if os.path.isfile('./check_integrity.cfg'):
        print "Reading config from " + os.getcwd() + \
            '/check_integrity.cfg'
        read = config.read('./check_integrity.cfg')
    else:
        print "No config file in " + os.getcwd()

    # For ease of use, stash all our config stuff into variables.
    # TODO(mvoelker): This is silly.  Use a dict for crying out loud.
    global baseurl, series_number, repomgmt_api_username
    global repomgmt_api_key, github_username, github_pass
    global bzr_tmp, pkg_tmp
    baseurl = config.get('check_integrity', 'baseurl')
    series_number = config.get('check_integrity', 'series_number')
    repomgmt_api_username = config.get('check_integrity',
        'repomgmt_api_username')
    repomgmt_api_key = config.get('check_integrity', 'repomgmt_api_key')
    github_username = config.get('check_integrity', 'github_username')
    github_pass = config.get('check_integrity', 'github_pass')
    bzr_tmp = config.get('check_integrity', 'bzr_tmp')
    pkg_tmp = config.get('check_integrity', 'pkg_tmp')


def get_and_extract_pkg(pkg, pkg_tmp):
    """
    Downloads and extracts a debian package via apt.  Note that
    it's up to you to make sure your apt sources are set up
    properly--this doesn't do any mangling of the sources list at
    all.
    """

    # Switch to /tmp and download the package.
    # Parse out the name of the file we grabbed.
    # It doesn't have to be exact (we'll wildcard the
    # the call to dpkg), which is good since the output
    # from apt may truncate long filenames.
    os.chdir('/tmp/')
    dl_msg = ''.join(
        os.popen('apt-get -y download ' + pkg).read())

    # Set up a regex to grock the filename (or a portion thereof)
    # from the output on stdout.
    version_regex = re.compile('Downloading\s+\S+\s+(\d+:)?(\S+)\s+')
    match = re.search(version_regex, dl_msg)

    # TODO(mvoelker): do some validation here.

    # Extract the package files.
    print "Getting version from " + dl_msg
    pkg_version = ''
    print "Extracting " + pkg + '_' + match.group(2)
    os.system(
        "dpkg -X " + pkg + '_' + match.group(2) + \
        "* " + pkg_tmp)


def get_changelog_sha(pkg, pkg_tmp):
    """
    Given an set of files extracted from a .deb, read the SHA
    of the most recent commit from the usr/share/doc/[package name]/
    changelog.Debian.gz file and return it.  Such a file is
    expected to contain entries that look something like this:

    quantum (2012.2.4-4-cisco1) folsom; urgency=low

      * Automated PPA build. Code revision:
        d62daa765cd6daa943da9432b43ed53cd7bd8c60. Packaging revision:
        chris.ricker@gmail.com-20130307181358-5u1max0kd1ti6onx.
    """

    # We do a two-part search: first we look for a preamble
    # that tells us a SHA is on the next line.  Then we
    # look for the SHA itself.

    # TODO(mvoelker): it occurs to me that this might be
    # unecessary...we may be able to make this a single-phase
    # search if the patterns are actually predictable.  I wasn't sure
    # if they were consistent when I originally wrote this bit.

    print "Reading %s/usr/share/doc/%s/changelog.Debian.gz" % (
        pkg_tmp, pkg)
    saw_preamble = False
    saw_sha = False
    preamble_regex = re.compile('Code revision:\s*$')
    sha_regex = re.compile(
        '^\s+(\w+)\.\s+Packaging revision:\s*$')

    # Read the changelog file and look for the preamble
    # and/or the SHA.
    for line in gzip.open(
        "%s/usr/share/doc/%s/changelog.Debian.gz" % (
        pkg_tmp, pkg)):

        # If we have seen the preamble, look for the SHA.
        if(saw_preamble):
            # We've seen the preamble, so this should be a SHA.
            match = re.search(sha_regex, line)
            if(match):
                # Found a SHA!  Wrap up.
                return match.group(1)
            else:
                # Hmm...that's odd.  If no SHA is here,
                # at least warn about it.
                print "Didn't find SHA after preamble in:"
                print line
        else:
            # We haven't seen the preamble yet, so look for it.
            match = re.search(preamble_regex, line)
            if(match):
                # There is is!  Next line should have a SHA.
                saw_preamble = True

    # If we got through the entire file and didn't see a SHA,
    # return something that says so.
    return False


# Set up a few things we'll need for later.
# Read a local config file if one exists.
read_config('./check_integrity.cfg')

# A dict in which we'll store SHA sums for packages.
problems = {}

# The string we'll use to authenticate repomgmt API calls.
auth_string = 'ApiKey ' + repomgmt_api_username + ':' + repomgmt_api_key

# The URL for the series we're querying.
series_url = baseurl + '/api/v1/series/' + series_number + '/'

# Clear out a couple of directories where we'll be
# putting files.
if os.path.isdir(bzr_tmp):
    shutil.rmtree(bzr_tmp)
if os.path.isdir(pkg_tmp):
    shutil.rmtree(pkg_tmp)

# Do an "apt-get update" to make sure we're getting the most
# recent packages when we start downloading them.
os.system('apt-get update')

# Do the query and parse the json we get back.
# This gives us a list of subscriptions in the series.
# We'll then query each subscription.  This query is loosely equivalent
# to something like:
#
# curl -v -H "Content-type: aplication/json" -X GET --header \
# 'Authorization: ApiKey mvoelker:1111111111111111111111111111111111111111'\
# http://apt.ctocllab.cisco.com/api/v1/series/1/
response = requests.get(url=series_url, headers={'Authorization': auth_string})
subscription_data = response.json()

# TODO(mvoelker): add graceful error handling here rather than
# letting the script crash if the API call didn't work.

# Iterate through the subscriptions we found.
for subscription_url in subscription_data['subscriptions']:

    # Get the subscription info with another API call.  This
    # call is loosely equivalent to something like:
    #
    # curl -v -H "Content-type: aplication/json" -X GET --header \
    # 'Authorization: ApiKey mvoelker:111111111111111111111111'\
    # http://apt.ctocllab.cisco.com/api/v1/subscription/212/
    print "Checking subscription for " + baseurl + subscription_url
    sub_url = baseurl + subscription_url
    response = requests.get(
        url=sub_url, headers={'Authorization': auth_string})
    sub_data = response.json()

    # Use the package_source attribute to make another API call
    # to acquire details about the package source.  This call
    # is loosely equivalent to something like:
    #
    # curl -v -H "Content-type: aplication/json" -X GET --header \
    # 'Authorization: ApiKey mvoelker:111111111111111111111111'\
    # http://apt.ctocllab.cisco.com/api/v1/packagesource/61/
    url = baseurl + sub_data['package_source']
    response = requests.get(
        url=url, headers={'Authorization': auth_string})
    src_data = response.json()

    # Grab some data:
    # * The code_url is the GitHub URL of the repo we need to check.
    #   It will probably have a branch name following a '#' at the
    #   end of the URL.
    # * The last_seen_code_rev tells us what repomgmt thinks is the
    #   SHA of the latest revision of code in that branch.
    # * The packaging_url tells us where the packaging spec lives.
    #   As of this writing, that's usually going to be in bzr on
    #   on Launchpad somewhere.
    # * The name tells us what the name of the package source is.
    #   This is basically just for human readability.
    code_url = src_data['code_url']
    last_seen_rev = src_data['last_seen_code_rev']
    packaging_url = src_data['packaging_url']
    pkg_name = src_data['name']

    # If the code URL doesn't exist, we must bail.
    if code_url == '':
        print "WARNING: " + src_data['name'] + \
            " is unclean: no code URL in package subscription."
        problems[pkg_name] = {}
        problems[pkg_name]['repomgmt_sha'] = last_seen_rev
        problems[pkg_name]['github_sha'] = 'No code URL found'
        problems[pkg_name]['subscription_url'] = subscription_url
        continue

    # We now need to download and packages, check their
    # SHA hashes, and verify that they match the last_seen_code_rev
    # reported by repomgmt.  We then also need to check that against
    # github.  All three should match.  If so, the package is
    # assumed clean.
    is_clean = True

    # Let's start by querying GitHub.  The code_url provided by
    # repomgmt isn't an API address though, so we need to munge it a bit.
    github_url, git_branch = code_url.split('#', 1)
    url_re = re.compile('github.com')
    github_url = url_re.sub('api.github.com/repos', github_url)
    url_re = re.compile('\.git$')
    github_url = url_re.sub('', github_url)
    github_url += '/commits?sha=' + git_branch

    # Ok, now we have an API URL to query.  Query it.
    github_resp = requests.get(
        url=github_url,
        auth=(github_username, github_pass)
        )
    git_data = github_resp.json()

    # Search through what was returned to find the newest commit
    # (by committer date, not author date).
    newest_commit = {}
    newest_commit_date = '1987-01-01T00:00:00Z'
    for commit in git_data:
        if commit['commit']['committer']['date'] > newest_commit_date:
            newest_commit = commit
            newest_commit_date = \
                commit['commit']['committer']['date']

    # Just for debugging, say if we got a SHA.
    if 'sha' in newest_commit:
        print "Got valid reply"
        github_sha = newest_commit['sha']
    else:
        print "Didn't find a SHA in the data returned!" + git_data[0]

    # Does the SHA we got from GitHub match what repomgmt thinks
    # is the latest?  If not, we have a problem to flag.
    if (github_sha != last_seen_rev):
        # Say what's up.
        print "WARNING: " + src_data['name'] + " is unclean!"
        print "repomgmt last_seen_code_rev = " + last_seen_rev
        print "Github latest code rev      = " + github_sha
        print "(using " + github_url + ")"

        # File away the error for printing in a summary later.
        is_clean = False
        pkg_name = src_data['name']
        problems[pkg_name] = {}
        problems[pkg_name]['repomgmt_sha'] = last_seen_rev
        problems[pkg_name]['github_sha'] = github_sha
        problems[pkg_name]['subscription_url'] = subscription_url
        print "\tInstalling " + pkg
    else:
        # The SHA's match.  Good.
        print "repomgmt last_seen_code_rev matches github for " \
            + src_data['name']

    # Now we have to figure out the names of the packages built
    # from this package source, download them, and extract them
    # so we can check the SHA reported in it's changelog.Debian.gz file.
    # To do that we need to read some data from the package spec.

    # Fetch the package spec.
    print "Branching package spec branch " + src_data['packaging_url']
    remote_branch = Branch.open(src_data['packaging_url'])
    local_branch = remote_branch.bzrdir.sprout(
        bzr_tmp).open_branch()

    # Read the debian/control file and look for "Package: <somename>"
    packages = []
    pkg_regex = re.compile('^\s*Package:\s*(\S+)\s*$')
    for line in open("%s/debian/control" % bzr_tmp, 'r'):
        match = re.search(pkg_regex, line)
        if(match):
            # Found one...parse the package name.
            packages.append(match.group(1))
            print match.group(1) + " is built from this."

    # Now we need to download each of the packages
    # and check the SHA sums in their changelog.Debian.gz files.
    # Note that some packages create symlinks to the changelog files
    # provided by other packages...which means the easiest thing
    # to do is to download and extract files for each package first,
    # Then go check the changelog files.

    # First, download and extract files from each package.
    for pkg in packages:
        get_and_extract_pkg(pkg, pkg_tmp)

    # At this point we've extracted files from all the packages.
    # Now we can go through and read SHA's from each changelog.
    for pkg in packages:
        # Grab the SHA from the changlog.
        pkg_sha = get_changelog_sha(pkg, pkg_tmp)

        # Did we actually find one?
        if pkg_sha == False:
            # Hmm...no SHA.  Something is amiss.  Say so.
            print "Warning: Never saw a SHA in " +\
                "%s/usr/share/doc/%s/changelog.Debian.gz" % (
                        pkg_tmp, pkg)

            # If you want to stop and debug a bit before the
            # files get deleted, uncomment the line below.
            #nb = raw_input('Press return to continue ')

            # File away this info so we can print it in a
            # summary later.
            problems[pkg] = {}
            problems[pkg]['pkg_sha'] = 'Not Found'
            problems[pkg]['repomgmt_sha'] = last_seen_rev
            problems[pkg]['github_sha'] = github_sha
            problems[pkg]['subscription_url'] = subscription_url
            continue
        else:
            # Does the SHA from the changelog match what we
            # saw from Github?
            if pkg_sha != github_sha:
                print "WARNING: %s is unclean (changelog " \
                    + "SHA does't match GitHub)!"
                print "GitHub SHA:"
                print github_sha
                print "Package changelog SHA:"
                print pkg_sha

                # File this info away to summarize later.
                is_clean = False
                problems[pkg] = {}
                problems[pkg]['pkg_sha'] = pkg_sha
                problems[pkg]['repomgmt_sha'] = last_seen_rev
                problems[pkg]['github_sha'] = github_sha

            # Does the SHA from the changelog match what we
            # saw from repomgmt?
            if pkg_sha != last_seen_rev:
                print "WARNING: %s is unclean (changelog " \
                    + "SHA doesn't match repomgmt)!"
                print "Repomgmt SHA:"
                print last_seen_rev
                print "Package changelog SHA:"
                print pkg_sha

                # File this info away to summarize later.
                is_clean = False
                problems[pkg] = {}
                problems[pkg]['pkg_sha'] = pkg_sha
                problems[pkg]['repomgmt_sha'] = last_seen_rev
                problems[pkg]['github_sha'] = github_sha

    # We've now finished checking all the packages for this source.
    print "Finished checking packages associated with " + src_data['name']

    # Clean up after ourselves.
    if os.path.isdir(bzr_tmp):
        # Delete the temporary directory in which we
        # branched the package spec.
        shutil.rmtree(bzr_tmp)
    if os.path.isdir(pkg_tmp):
        # Delete the temporary directory in which we
        # extracted package sources.
        shutil.rmtree(pkg_tmp)

    # TODO(mvoelker): Also delete the .deb files.

# Print up a summary.
print "============"
print "SUMMARY"
print "============"
for item in problems:
    print item
    if 'github_sha' in problems[item]:
        print "\tGithub SHA:\t" + problems[item]['github_sha']
    if 'repomgmt_sha' in problems[item]:
        print "\tRepomgmt SHA:\t" + problems[item]['repomgmt_sha']
    if 'pkg_sha' in problems[item]:
        print "\tPackage SHA:\t" + problems[item]['pkg_sha']
    if 'subscription_url' in problems[item]:
        print "\tSubscription URL:\t" + \
            problems[item]['subscription_url']
