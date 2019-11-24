from pathlib import Path
import multiprocessing
import collections
import subprocess
import filecmp
import random
import string
import time
import re
import os

try:
    from tqdm import tqdm
except ImportError as e:
    def tqdm(it):
        it = list(it)
        def p(i): return round(i/len(it)*100)
        for i, x in enumerate(it):
            if p(i) != p(i-1):
                print("{: >3}%...".format(p(i)), end="\r")
            yield x
        print("100%... done")
import click


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


VerifyStatus = collections.namedtuple('VerifyStatus', ['ok', 'time', 'case', 'meta'])

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
        try:
            start = time.time()
            subprocess.run(
                self.app, shell=True, timeout=self.timeout,
                stdin=open(self.input_of(case), 'rb'), stdout=open(got, 'wb'),
            )
            end = time.time()
        except subprocess.TimeoutExpired as e:
            return VerifyStatus(None, self.timeout, case, {'timeout': True})

        meta = {'timeout': False}
        v = self.verify(case, got)
        if isinstance(v, dict):
            meta = v
            del meta['ok']
            v = v['ok']

        return VerifyStatus(v, round(end - start, 6), case, meta)

class IdenticalOutputVerifier(Verifier):
    def verify(self, case, got):
        return filecmp.cmp(got, self.output_of(case))

class LooseOutputVerifier(Verifier):
    def verify(self, case, got):
        a = open(got).read()
        b = open(self.output_of(case)).read()
        return a.split() == b.split()

class CheckerVerifier(Verifier):
    def verify(self, case, got):
        param = [self.checker, self.input_of(case), got]
        if self.out_path is not None:
            param.insert(2, self.output_of(case))
        process = subprocess.run(param, shell=True, timeout=self.timeout)
        return {'ok': process.returncode == 0, 'code': process.returncode}


available_verifiers = {
    'identical': IdenticalOutputVerifier,
    'loose': LooseOutputVerifier,
    'checker': CheckerVerifier
}


def natural_sort(it):
    def tryint(x):
        try:
            return int(x)
        except:
            return x
    def nkey(x):
        return tuple(tryint(c) for c in re.split('([0-9]+)', str(x)))
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

def verify_job(inputs, verifier, verbose):
    stati = []
    for i in inputs:
        status = verifier(i.stem)
        stati.append(status)
        if verbose > 0:
            print(status)
        if verbose < 0: # multiprocessing
            print(status.case)
    return stati

def verify_job_mp(args):
    return verify_job(*args, -1)

@click.command()
@click.argument('app')
@click.argument('tests')
@click.option('-o', '--outputs', default=None, help="Output file directory. Same as TESTS by default.")
@click.option('-d', '--order', 's_order', default='natural', help="Test ordering: lexicographical, [natural], random, filesize. Supports prefix completion.")
@click.option('-e', '--verify', 's_verify', default='loose', help="Verifier: identical, [loose], checker. Loose verification compares only words (whitespace-separated strings). Supports prefix completion.")
@click.option('-c', '--checker', default=None, help="Checker program. Should take input filename, expected out filename and received out filename as arguments and return a nonzero status code if the output is not correct.")
@click.option('-t', '--timeout', default=600, type=float, help="Program timeout length.")
@click.option('-p', '--processes', default=1, type=int, help="Enable multiprocessing with given process count.")
@click.option('-v', '--verbose', default=0, count=True, help="Controls verbosity level.")
def main(app, tests, outputs, s_order, s_verify, checker, timeout, processes, verbose):
    """
    Test the program APP using test cases provided in the TESTS directory.
    """

    s_verify = find_prefixwise(available_verifiers.keys(), s_verify)
    s_order = find_prefixwise(available_orderings.keys(), s_order)
    verifier_t = available_verifiers[s_verify]
    order = available_orderings[s_order]

    in_path = Path(tests)
    out_path = Path(outputs if outputs is not None else tests)

    if checker and s_verify != 'checker':
        print('[?] Checker parameter provided but verifier is not checker.')

    verifier_params = (app, in_path, out_path, checker, timeout)

    inputs = order(in_path.glob('*.in'))

    if processes == 1:
        if not verbose:
            inputs = tqdm(inputs)
        stati = verify_job(inputs, verifier_t(*verifier_params), verbose)
    else:
        with multiprocessing.Pool(processes) as pool:
            data = [(inputs[start::processes], verifier_t(*verifier_params)) for start in range(processes)]
            stati = sum(pool.map(verify_job_mp, data), [])

    correct = sum(bool(s.ok) for s in stati)
    total = len(stati)
    print("Correct: {}/{} ({}%)".format(correct, total, round(correct/total*100, 1)))

    if correct == total:
        print("AC! :)")
    else:
        for s in stati:
            if not s.ok:
                print(s)



if __name__ == "__main__":
    main()
