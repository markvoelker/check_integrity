This script is used to validate that three tools in our package management
system are all using the same code.  It essentially does this:

* Talk to repomgmt and get all subscriptions in a given series.

* For each one, get info from repomgmt about that subscription,
including the relevant code branch in github, last seen code rev,
packaging URL, etc.

* For each one, query GitHub and find the SHA of the most recent
commit.  Verify that it matches what repomgmt said was the last seen
code rev.

* For each one, read the package spec and get a list of packages that
are built for that subscription.  Download each one and extract it's
changelog.Debian.gz file.  Read the SHA of the latest commit from it
and verify that matches the SHA we got from GitHub and repomgmt.

To use the script, simply set up a config file (a sample is included), put
it in the same directory as the script, and run the script.
