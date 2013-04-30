#!/usr/bin/env python
import sys
import os
import subprocess
import fnmatch
import glob
import re
import optparse
import getpass
import json
import urllib
import cgi

helpDescription = "Query Bugzilla for a set of bug numbers and show the ones that are fixed."

helpConfigs = """
Config files for %prog tell it which bugs to watch
or where to find bug numbers:
  bug N
  filenames dir
  contents filename-pattern
  contents leaf-pattern in search-path
  include other-manfiest

You can also specify a single place to look for bugs on the command line
If you do this in a shell, place quotes around filename patterns:
%prog contents "*.list" in ~/trees/mozilla-central/
""".replace("%prog", os.path.basename(sys.argv[0]))

# When a config file uses "contents", these heuristics control whether
# a mention of a bug is counted.
bugMentionRE = re.compile(
    r"(?<!see )(?<!post )(?<!removed in )(?<!added in )(?<!landed in )(?<!implemented in )(?<!discussion in )(?<!inspired by )(?<!test )(?<!tests for )(?<!test for )(?<!re-read )(?<! per )"
    + r"(?:"
    + r"(?:bug \#?(\d+)(?!\d)(?! comment))"
    + r"|"
    + r"(?:https?://bugzilla\.mozilla\.org/show_bug\.cgi\?id\=(\d+)(?!\d)(?!#))"
  + r")"
  + r"(?! \(fixed)(?! \(wontfix)(?! \(branch)(?! \(\d+ branch)(?! test code)"
  , re.IGNORECASE)

assert ('123456',None) == bugMentionRE.search("FIXME: bug 123456").groups()
assert ('123456',None) == bugMentionRE.search("Bug 123456").groups()
assert ('123456',None) == bugMentionRE.search("Bug 123456 will fix this").groups()
assert ('123456',None) == bugMentionRE.search("Work around bug 123456:").groups()
assert ('123456',None) == bugMentionRE.search("Bug 123456 (makes this testcase crash)").groups()
assert (None,'123456') == bugMentionRE.search("http://bugzilla.mozilla.org/show_bug.cgi?id=123456").groups()
assert (None,'123456') == bugMentionRE.search("https://bugzilla.mozilla.org/show_bug.cgi?id=123456").groups()
assert                not bugMentionRE.search("see bug 123456")
assert                not bugMentionRE.search("implemented in bug 123456")
assert                not bugMentionRE.search("test for bug 123456")
assert                not bugMentionRE.search("Bug 123456 comment 4")
assert                not bugMentionRE.search("Bug 123456 (fixed in rev ....)")
assert                not bugMentionRE.search("Bug 123456 (branch)")
assert                not bugMentionRE.search("https://bugzilla.mozilla.org/show_bug.cgi?id=123456#c7")
assert                not bugMentionRE.search("https://bugzilla.mozilla.org/show_bug.cgi?id=123456 (fixed)")


def bugSearch(search, bugzillaLoginPrefix):
    # Uses https://wiki.mozilla.org/Bugzilla:REST_API
    # Uses https://wiki.mozilla.org/Bugzilla:REST_API:Search
    # Uses curl, because checking https certs within Python is hard.
    url = "https://api-dev.bugzilla.mozilla.org/latest/bug?" + bugzillaLoginPrefix + search
    p = subprocess.Popen(["curl", url], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (out, err) = p.communicate()
    if p.returncode != 0:
        raise Exception("curl exited with code " + str(p.returncode) + " and wrote the following on stderr: \n" + err)
    try:
        return json.loads(out)
    except ValueError as e:
        print "JSON decoding error?"
        print url
        print out
        raise

def prettyPrint(x):
    return json.dumps(x, sort_keys=True, indent=4)

def findFiles(base, pattern):
    if len(pattern) == 1:
        return glob.glob(os.path.join(base, os.path.expanduser(pattern[0])))
    elif len(pattern) == 3 and pattern[1] == "in":
        r = []
        searchPath = os.path.join(base, os.path.expanduser(pattern[2]))
        for (dirpath, dirnames, filenames) in os.walk(searchPath):
            for filename in filenames:
                if fnmatch.fnmatch(filename, pattern[0]):
                    r.append(os.path.join(dirpath, filename))
        return r
    else:
        print "### Warning: expected [filename-pattern] or [leaf-pattern in search-path] but saw " + repr(pattern)

def readConfig(filename):
    with file(filename) as f:
        parseConfig(os.path.abspath(filename), os.path.dirname(filename), f)

def parseConfig(absFilename, base, f):
    recentComment = ""
    lineNum = 0
    for line in f:
        lineNum += 1
        line = line.strip()
        if line.startswith("#"):
            recentComment += "\n  " + line
        else:
            if len(line) > 0:
                toks = line.split(" ")
                cmd = toks[0].lower()
                if cmd == "bug":
                    addBug(expectList, toks[1], absFilename + ":" + str(lineNum) + recentComment)
                elif cmd == "include" or cmd == "contents" or cmd == "filenames":
                    rs = findFiles(base, toks[1:])
                    if len(rs) == 0:
                        print "### Warning: no matches for " + repr(toks[1:])
                    for r in rs:
                        if verbose:
                            print cmd + " " + r
                        if cmd == "include":
                            readConfig(r)
                        if cmd == "contents":
                            scanFileForBugMentions(r)
                        if cmd == "filenames":
                            readFilenames(r)
                else:
                    print "### Warning: unrecognized config entry " + repr(cmd)
            recentComment = ""

def scanFileForBugMentions(filename):
    absfn = os.path.abspath(filename)
    if htmlOutput:
        prettyfn = absfn.replace(mcLocal, "")
        mxrfn = absfn.replace(mcLocal, mcMXR)
        blamefn = absfn.replace(mcLocal, mcBlame)
    with file(filename) as f:
        lineno = 0
        for line in f:
            lineno += 1
            matches = bugMentionRE.findall(line)
            for m in matches:
                bugid = m[0] if m[0] else m[1]
                if htmlOutput:
                    message = '<li><a href="%s#%d">%s, line %d</a> (<a href="%s#l%d">blame</a>) -- %s</li>' % (mxrfn, lineno, prettyfn, lineno, blamefn, lineno, cgi.escape(line.strip()))
                else:
                    message = "%s:%d\n  %s" % (absfn, lineno, line.strip())
                addBug(expectList, bugid, message)

def readFilenames(directory):
    for fn in os.listdir(directory):
        numbers = re.findall(r"\d{3,}", fn)
        for n in numbers:
            addBug(expectList, n, "Filename " + os.path.join(directory, fn))

def addBug(list, bugid, message):
    newBug = False
    if bugid not in list:
        list[bugid] = []
        newBug = True
    if verbose:
        print "  " + ("adding" if newBug else "  also") + " bug " + bugid + " (" + repr(message) + ")"
    list[bugid].append(message)

def initHTML():
    global mcLocal, mcMXR, mcBlame
    mcLocal = os.path.expanduser("~/trees/mozilla-central/")
    mcMXR = "http://mxr.mozilla.org/mozilla-central/source/"
    mcRev = subprocess.Popen(["hg", "-R", mcLocal, "parent", "--template={node|short}"], stdout=subprocess.PIPE).communicate()[0]
    mcBlame = "http://hg.mozilla.org/mozilla-central/annotate/" + mcRev + "/"


def main():
    global htmlOutput
    global verbose
    global expectList

    def usage():
        optparser.print_help()
        print helpConfigs
        sys.exit(0)

    optparser = optparse.OptionParser(description=helpDescription, usage="usage: %prog [options] configfile|command", add_help_option=False)
    optparser.add_option("-v", "--verbose",  action="store_true", dest="verbose",       help="show which files are being read")
    optparser.add_option("-H", "--html",     action="store_true", dest="htmlOutput",    help="output HTML with links to Bugzilla, MXR, HG Blame")
    optparser.add_option("-l", "--login",                         dest="bugzillaLogin", metavar="@", help="use this Bugzilla username (prompt for password)")
    optparser.add_option("-b", "--bug",                           dest="bugID",         help="rather than querying bugzilla, show expect-entries for this bug id")
    optparser.add_option("-h", "--help",     action="callback",                         help="show this help message and exit", callback = lambda option, opt, value, parser: usage())
    (options, args) = optparser.parse_args()
    htmlOutput = options.htmlOutput
    verbose = options.verbose
    bugzillaLogin = options.bugzillaLogin

    if bugzillaLogin:
        bugzillaPassword = getpass.getpass("Bugzilla password for %s: " % bugzillaLogin)
        bugzillaLoginPrefix = "username=" + urllib.quote_plus(bugzillaLogin) + "&password=" + urllib.quote_plus(bugzillaPassword) + "&"
    else:
        bugzillaLoginPrefix = ""

    if htmlOutput:
        initHTML()

    # Map from bug number to the reasons it's listed
    # e.g. {12345: ['assertions.txt line 10', 'crashes.txt line 2']}
    expectList = dict()

    if len(args) == 0:
        usage()
    elif len(args) == 1:
        readConfig(args[0])
    else:
        parseConfig("args", os.getcwd(), [" ".join(args)])

    commaids = ",".join(expectList.keys())
    if options.bugID:
        bugs = [{'id': options.bugID, 'summary': "?", 'resolution': "(specified on command line)"}]
    elif len(expectList):
        if verbose:
            print("Querying Bugzilla regarding " + str(len(expectList)) + " bugs which we expect to be open:")
            print(commaids)
        r = bugSearch("id=" + commaids + "&field0-0-0=resolution&type0-0-0=not_regex&value0-0-0=^$&type0-0-1=substring&value0-0-1=fixed-in-tracemonkey&field0-0-1=status_whiteboard", bugzillaLoginPrefix)
        if r.get("error"):
            print "Error from Bugzilla API:"
            print "  " + r.get("message")
            sys.exit(0)
        bugs = r.get("bugs")
    else:
        print "No bugs to check."
        sys.exit(0)

    if not bugs:
        print "All " + str(len(expectList)) + " bugs are still open."
        sys.exit(0)

    if verbose:
        print "Got " + str(len(bugs)) + " bug(s)"

    if htmlOutput:
        print '<ul>'
    else:
        print ""

    for bug in bugs:
        id = bug.get("id")
        whyShown = bug.get("resolution")
        if whyShown == "":
            whyShown = "fixed-in-tracemonkey"
        if htmlOutput:
            print ('<li style=margin-bottom:1em>%s: <a href="%s">Bug %s</a> -- %s' %
                (whyShown, "https://bugzilla.mozilla.org/show_bug.cgi?id="+id, id, cgi.escape(bug.get("summary"))))
            print '<ul>'
            for message in expectList[id]:
                print message
            print '</ul>'
            print '</li>'
        else:
            print "Bug " + id + " is " + whyShown
            for message in expectList[id]:
                print message
            print ""

    if htmlOutput:
        print '</ul>'


if __name__ == "__main__":
    main()
