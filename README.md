# taucheck

The successor of thetacheck. An utility for testing a program with a set of input and output files. Based around the SIO2 (Polish online judge) ecosystem, but is somewhat generic.

Requires `click` for the CLI to work.

```
Usage: taucheck.py [OPTIONS] APP TESTS

  Test the program APP using test cases provided in the TESTS
  directory.

Options:
  -o, --outputs TEXT       Output file directory. Same as TESTS by
                           default.
  -d, --order TEXT         Test ordering: lexicographical,
                           [natural], random, filesize. Supports
                           prefix completion.
  -e, --verify TEXT        Verifier: identical, [loose], checker.
                           Loose verification compares only words
                           (whitespace-separated strings).
                           Supports prefix completion.
  -c, --checker TEXT       Checker program. Should take input
                           filename, expected out filename and
                           received out filename as arguments and
                           return a nonzero status code if the
                           output is not correct.
  -t, --timeout FLOAT      Program timeout length.
  -p, --processes INTEGER  Enable multiprocessing with given
                           process count.
  -f, --fatal              Stop testing when a non-ok status is
                           encountered.
  -v, --verbose            Controls verbosity level.
  --help                   Show this message and exit.
```
