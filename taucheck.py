from pathlib import Path
import multiprocessing
import collections
import subprocess
import difflib
import filecmp
import pprint
import random
import string
import time
import re
import os

try:
    from tqdm import tqdm
except ImportError as e:
    def tqdm(it, total=None):
        if total is None:
            total = it.__length_hint__()
        def p(i): return round(i/total*100)
        for i, x in enumerate(it):
            if p(i) != p(i-1):
                print("{: >3}%...".format(p(i)), end="\r")
            yield x
        print("100%... done")

try:
    import colorama
    colorama.init()
    Fore, Back, Style = colorama.Fore, colorama.Back, colorama.Style
except ImportError as e:
    class Fakerama:
        def __getattribute__(self, attr):
            return ''
    Fore, Back, Style = Fakerama(), Fakerama(), Fakerama()

import click

# Temporary files: a decorated function receives an additional parameter,
# which is a random temporary filename. The file is always deleted after
# leaving the function.
def temp_filename(suffix='.tmp', dlen=4):
    d = "".join(random.choice(string.hexdigits) for _ in range(dlen))
    return d + suffix
def with_temporary_file(func, suffix='.tmp', dlen=4):
    def wrapped(*args, **kwargs):
        tmp = temp_filename(suffix, dlen)
        try:
            return func(*args, tmp, **kwargs)
        finally:
            os.remove(tmp)
    return wrapped


def result_diff(expected, got):
    a, b = expected.splitlines(), got.splitlines()
    s = difflib.SequenceMatcher(None, a, b)
    r = ''
    for tag, i1, i2, j1, j2 in s.get_opcodes():
        if tag == 'equal':
            continue
        r += '{:7}   a[{}:{}] --> b[{}:{}] {!r:>8} --> {!r}\n'.format(
            tag, i1, i2, j1, j2, a[i1:i2], b[j1:j2]
        )
    return r


# Verifiers are objects that take care of testing singular cases.
# All of them run the app, but they have different ways of deciding
# if the result is correct. A VerifyStatus object should be a summary
# of the results for a given case.

VerifyStatus = collections.namedtuple('VerifyStatus', ['ok', 'time', 'case', 'meta'])
# `ok` is `True` if everything was correct, `False` if the result is wrong,
# None otherwise (reason should be specified in `meta`)
# `case` is the case name (stem of the input file), `time` is the time taken.

class Verifier:
    def __init__(self, app, in_path, out_path=None, checker=None, timeout=600):
        self.app, self.in_path, self.out_path = app, in_path, out_path
        self.checker, self.timeout = checker, timeout

    def input_of(self, case):
        return (self.in_path / case).with_suffix('.in')

    def output_of(self, case):
        return (self.out_path / case).with_suffix('.out')

    def verify(self, case):
        return NotImplemented

    @with_temporary_file
    def __call__(self, case, got):
        # got is a temporary file for program output
        try:
            start = time.time()
            process = subprocess.run(
                self.app, shell=True, timeout=2*self.timeout, check=True,
                stdin=open(self.input_of(case), 'rb'), stdout=open(got, 'wb')
            )
            end = time.time()
        except subprocess.TimeoutExpired as e:
            return VerifyStatus(None, 2*self.timeout, case, {'timeout': True, 'code': None})
        except subprocess.CalledProcessError as e:
            return VerifyStatus(None, round(time.time() - start, 6), case, {'timeout': False, 'code': e.returncode})

        meta = {'timeout': end-start > self.timeout, 'code': process.returncode}
        v = self.verify(case, got)
        # In the simple case, verify returns only the status (`ok`). Otherwise,
        # it is a dictionary containing the status and metadata.
        if isinstance(v, dict):
            meta.update(v)
            del meta['ok']
            v = v['ok']

        return VerifyStatus(v, round(end - start, 6), case, meta)


class IdenticalOutputVerifier(Verifier):
    def verify(self, case, got):
        if filecmp.cmp(got, self.output_of(case)):
            return True
        else:
            a = open(self.output_of(case)).read()
            b = open(got).read()
            return {'ok': False, 'diff': result_diff(a, b)}


class LooseOutputVerifier(Verifier):
    def verify(self, case, got):
        a = open(self.output_of(case)).read()
        b = open(got).read()
        if a.split() == b.split():
            return True
        else:
            diff = difflib.context_diff(a, b, fromfile='expected', tofile='got')
            return {'ok': False, 'diff': result_diff(a, b)}

# TODO: test this
class CheckerVerifier(Verifier):
    def verify(self, case, got):
        param = [self.checker, self.input_of(case), got]
        if self.out_path is not None:
            param.insert(2, self.output_of(case))
        process = subprocess.run(param, shell=True, timeout=self.timeout, capture_output=True)
        return {'ok': process.returncode == 0, 'checkcode': process.returncode, 'comment': process.stdout.decode()}


available_verifiers = {
    'identical': IdenticalOutputVerifier,
    'loose': LooseOutputVerifier,
    'checker': CheckerVerifier
}


# Input filenames are sorted using these.

def tryint(x):
    try:
        return int(x)
    except:
        return x
def nkey(x):
    return tuple(tryint(c) for c in re.split('([0-9]+)', str(x)))
def natural_sort(it):
    return sorted(it, key=nkey)

def shuffled(it):
    it = list(it)
    random.shuffle(it)
    return it

def filesize_sort(it):
    return sorted(it, key=os.path.getsize)

available_orderings = {
    'lexicographical': sorted,
    'natural': natural_sort,
    'random': shuffled,
    'size': filesize_sort
}


# Finds the string in `it` which has the longest common prefix with `string`
# or decides that there is no good result.
def find_prefixwise(it, string):
    dec = []
    for key in it:
        if len(key) < len(string):
            continue
        elif string == key:
            return string
        k = 0
        while k < len(string):
            if key[k] == string[k]:
                k += 1
            else:
                break
        dec.append((k, key))
    dec.sort()
    if all(k == 0 for k, _ in dec):
        raise ValueError("No matching key for `{}`".format(string))
    if len(dec) >= 2 and dec[-2][0] == dec[-1][0]:
        same = [key for k, key in dec if k == dec[-1][0]]
        raise ValueError("Ambiguous prefix `{}` of {}".format(string, same))
    return dec[-1][1]

# Handles the verifier doing all the work.
def verify_job(cases, verifier, verbose):
    for i in cases:
        status = verifier(i)
        yield status

# Version for multiprocessing (ignore verbose flag, logging is messed up anyway)

process_verifier = None
def initialize_worker(vertype, param):
    global process_verifier
    process_verifier = vertype(*param)

def verify_single(case):
    return process_verifier(case)


def test_status_acronym(status):
    ret = None
    form = ""
    if status.ok is True:
        ret, form = " OK", Fore.GREEN
    elif status.ok is False:
        ret, form = " WA", Fore.RED
    else:
        if status.meta['timeout']:
            ret = "TLE"
        else:
            ret = " ??"
            form = Fore.MAGENTA + Style.DIM
    if status.meta['timeout']:
        form = Fore.YELLOW + Style.BRIGHT
    return form + ret + Style.RESET_ALL

def print_summary(stati, verbose):
    print("===\nTest summary\n===")
    for s in stati:
        if not verbose and s.ok:
            continue
        code = s.meta['code'] if 'code' in s.meta and s.meta['code'] is not None else '??'
        print("- {} {} ({: >6.3f}s) -> {}{}".format(test_status_acronym(s), s.case, s.time, code, " (check: {})".format(s.meta['checkcode']) if 'checkcode' in s.meta else ""))
        if verbose:
            if 'diff' in s.meta:
                if len(s.meta['diff']) > 512:
                    print("[..diff too long, snip..]")
                else:
                    pprint.pprint(s.meta['diff'])
            if 'comment' in s.meta and s.meta['comment']:
                print("Checker (code {}) comment:")
                print(s.meta['comment'])

# TODO: handle wrapper callers like `oiejq`
# TODO: handle online test generation
@click.command()
@click.argument('app')
@click.argument('tests')
@click.option('-o', '--outputs', default=None, help="Output file directory. Same as TESTS by default.")
@click.option('-d', '--order', 's_order', default='natural', help="Test ordering: lexicographical, [natural], random, filesize. Supports prefix completion.")
@click.option('-e', '--verify', 's_verify', default='loose', help="Verifier: identical, [loose], checker. Loose verification compares only words (whitespace-separated strings). Supports prefix completion.")
@click.option('-c', '--checker', default=None, help="Checker program. Should take input filename, expected out filename and received out filename as arguments and return a nonzero status code if the output is not correct.")
@click.option('-t', '--timeout', default=600, type=float, help="Program timeout length.")
@click.option('-p', '--processes', default=1, type=int, help="Enable multiprocessing with given process count.")
@click.option('-f', '--fatal', default=False, is_flag=True, help="Stop testing when a non-ok status is encountered.")
@click.option('-v', '--verbose', default=0, count=True, help="Controls verbosity level.")
def main(app, tests, outputs, s_order, s_verify, checker, timeout, processes, fatal, verbose):
    """
    Test the program APP using test cases provided in the TESTS directory.
    """

    start = time.time()

    s_verify = find_prefixwise(available_verifiers.keys(), s_verify)
    s_order = find_prefixwise(available_orderings.keys(), s_order)
    verifier_t = available_verifiers[s_verify]
    order = available_orderings[s_order]

    in_path = Path(tests)
    if outputs is not None or s_verify != 'checker':
        out_path = Path(outputs if outputs is not None else tests)
    else:
        out_path = None

    if checker and s_verify != 'checker':
        print("[?] Checker parameter provided but verifier is not checker.")

    verifier_params = (app, in_path, out_path, checker, timeout)

    inputs = order(in_path.glob('*.in'))
    cases = [i.stem for i in inputs]

    stati = []
    job = None
    pool = None

    # Prepare job
    if processes == 1:
        job = verify_job(cases, verifier_t(*verifier_params), verbose)
    else:
        # A worker gets a verifier instance, so we don't
        # recreate it needlessly
        def winit():
            return initialize_worker(verifier_t, verifier_params)
        pool = multiprocessing.Pool(processes, initializer=winit)
        job = pool.imap_unordered(verify_single, cases)

    job = tqdm(job, total=len(cases))

    for status in job:
        if not status.ok and fatal:
            print("!@#")
            break
        stati.append(status)

    if pool is not None:
        pool.close()

    correct = sum(bool(s.ok) for s in stati)
    total = len(stati)
    print("Correct: {}/{} ({}%)".format(correct, total, round(correct/total*100, 1)))

    stati.sort(key=lambda s: nkey(s.case))

    if correct == total:
        print(Fore.GREEN + Style.BRIGHT + "AC! :)" + Style.RESET_ALL)

    print_summary(stati, verbose)

    end = time.time()

    print("Done in {:.3f}s".format(end - start))


if __name__ == "__main__":
    main()
