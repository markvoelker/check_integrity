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


def find(name, path):
   """
   Finds the first occurance of a given file within a given path.
   """
   for root, dirs, files in os.walk(path):
       if name in files:
           return os.path.join(root, name)


def get_license(pkg, pkg_tmp):
   """
   Given a set of files extracted from a .deb, look for one called
   LICENSE and see if you can determine what type of license it is.

   License files tend to take one of two forms.  For Puppet modules,
   we typically find the license in:
   /usr/share/puppet/modules/$pkg_name_sans_the_word_puppet/LICENSE
   
   For OpenStack components, we often find them in:
   /usr/share/doc/$pkg_name/copyright
   """
   # First figure out if we have a LICENSE file or, failing
   # that, a copyright file.
   license_filename = ''
   filenames = ['LICENSE', 'copyright']
   for filename in filenames:
       loc = find(filename, pkg_tmp)
       if loc:
           license_filename = loc
           break
   if license_filename == '':
       return 'license-file-not-found'
   try:
       f = open(license_filename, 'r')
       print "Reading %s/usr/share/puppet/modules/%s/LICENSE" % (
              pkg_tmp, pkg)
       agpl_regex = re.compile('GNU Affero General Public License')
       apl_part = re.compile('Apache License')
       apl_version_part = re.compile("Version (\d+)")
       apl_regex = re.compile("Apache License, Version (\d+)")
       gpl2_regex = re.compile('General Public License version 2')
       gpl3_regex = re.compile('refers to version 3 of the GNU General Public License')
       license_regex = re.compile('^License:\s*(.+)$')
       found_license_type = False
       linecount = 0
       for line in f:
           linebuffer = ''
           linebuffer_number = 0
           linecount += 1
           match = re.search(agpl_regex, line)
           if match:
               print "\tappears to be AGPL licensed."
               found_license_type = True
               return 'AGPL'
           match = re.search(apl_part, line)
           if match:
              print "\tappears to APL licenesed, fidning version..."
              linebuffer = 'APL'
           match = re.search(apl_version_part, line)
           if match:
               if linebuffer == 'APL':
                   if linecount == linebuffer_number + 1:
                       print "\t\tappears to be APL $s." % (match.group(1))
                       linebuffer = ''
                       return "APL-%s" % (match.group(1))
           match = re.search(apl_regex, line)
           if match:
               print "\tappears to be APL %s licensed." % (match.group(1))
               found_license_type = True
               return "APL-%s" % (match.group(1))
           match = re.search(gpl2_regex, line)
           if match:
               print "\tappears to be GPLv2 licensed."
               found_license_type = True
               return 'GPLv2'
           match = re.search(gpl3_regex, line)
           if match:
               print "\tappears to be GPLv3 licensed."
               found_license_type = True
               return 'GPLv3'
           match = re.search(license_regex, line)
           if match:
               print "\tlicense appears to be %s" % (match.group(1))
               found_license_type = True
               return match.group(1)
       if found_license_type == False:
           print "\thas a license, but I couldn't find what type."
           return 'found-but-type-unknown'
   except IOError:
       print "\t...WARNING: CAN'T OPEN LICENSE FILE!"
       return 'cannot-read-license-file'


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

# A dict in which we'll store license info.
licenses = {}

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
    for line in open('/tmp/code/debian/control', 'r'):
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

    # At this point we've extracted files from all the pacakges.
    # Now we can try to find licenses.
    for pkg in packages:
        # Grab the license type from the changelog.
        pkg_license = get_license(pkg, pkg_tmp)
        licenses[pkg] = pkg_license

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
type_counts = {}
for item in licenses:
    print item + "\tLicense: " + licenses[item]
    if licenses[item] in type_counts:
        type_counts[licenses[item]] += 1
    else:
        type_counts[licenses[item]] = 1

print "=============\nLicense Type Counts\n============="
for ltype in type_counts:
    print "%s %s" % (ltype, type_counts[ltype])
