from Mutate import *
from Util import *
from ParamData import *

import string
import argparse
import sys
import os
import stat

def main():

    parser = argparse.ArgumentParser(prog='poisson')
    subparsers = parser.add_subparsers(help='sub-command help')
    
    # create assembly pipeline parser
    parse_assm = subparsers.add_parser('assemble', help='assemble help')
    parse_assm.add_argument('output', help='output directory for files')
    parse_assm.add_argument('dirs', nargs='+', help='fast5 directories containing reads')
    parse_assm.add_argument('-p', '--params', default=None,
                        help='parameter file to use')
    parse_assm.add_argument('-n', '--threads', type=int, default=1)
    parse_assm.add_argument('-w', '--write', action="store_true", default=False,
                        help='write commands only, do not run')
    parse_assm.set_defaults(func=assemble)
    
    # create consensus-calling parser
    parse_cons = subparsers.add_parser('consensus', help='consensus help')
    parse_cons.add_argument('ref', help='reference fasta file')
    parse_cons.add_argument('bam', help='input BAM file')
    parse_cons.add_argument('dir', help='root fast5 directory')
    parse_cons.add_argument('-r', '--regions', default=None, nargs='+',
                        help='regions to correct (eg. 1000:3000 or header_name:1000:3000)')
    parse_cons.add_argument('-p', '--params', default=None,
                        help='parameter file to use')
    parse_cons.add_argument('-v', '--verbose', action="count", default=0,
                        help='output verbosity (0-2)')
    parse_cons.add_argument('-o', '--output', default=None,
                        help='output fasta file ("-" for stdout)')
    parse_cons.add_argument('-T', '--test', action="store_true", default=False,
                        help='test mode: seed with loaded sequence, output score as well')
    parse_cons.set_defaults(func=consensus)
    
    # and the variant-calling parser
    parse_var = subparsers.add_parser('variant', help='variant help')
    parse_var.add_argument('ref', help='reference fasta file')
    parse_var.add_argument('bam', help='input BAM file')
    parse_var.add_argument('dir', help='root fast5 directory')
    parse_var.add_argument('vars', help='variant sequences to test')
    
    parse_var.add_argument('-r', '--regions', default=None, nargs='+',
                        help='regions to correct (eg. 1000:3000 or header_name:1000:3000)')
    parse_var.add_argument('-p', '--params', default=None,
                        help='parameter file to use')
    parse_var.add_argument('-v', '--verbose', action="count", default=0,
                        help='output verbosity (0-2)')
    parse_var.set_defaults(func=variant)
                        
    # and finally the training parser
    parse_train = subparsers.add_parser('train', help='train help')
    parse_train.add_argument('ref', help='reference fasta file')
    parse_train.add_argument('bam', help='input BAM file')
    parse_train.add_argument('dir', help='root fast5 directory')

    parse_train.add_argument('-i', '--iter', type=int, default=30,
                        help='number of training iterations')
    parse_train.add_argument('-n', '--threads', type=int, default=4,
                        help='number of threads to use')
    parse_train.add_argument('-p', '--params', default=None,
                        help='parameter file to use')
    parse_train.set_defaults(func=train)
    
    args = parser.parse_args()
    args.func(args)
    

def assemble(args):

    # start by creating directory to hold all of the generated files
    if not os.path.isdir(args.output):
        os.mkdir(args.output)
    
    commands = ''

    # first step: extract and split fasta files
    commands += 'poissextract -p ' + string.join(args.dirs) + ' ' + os.path.join(args.output,'reads.fasta') + '\n'
    commands += 'poisssplit ' + os.path.join(args.output,'reads.fasta') + ' ' + str(args.threads) + '\n'
    
    # second step: align fasta files -> bams
    commands += ('ls ' + os.path.join(args.output,'reads.*.fasta') + ' | ' +
                    'parallel -P ' + str(args.threads) + ' poissalign {1} ' + 
                    os.path.join(args.output,'reads.fasta') + ' {1}\n')
                    
    paramstr = ''
    if args.params is not None:
        paramstr = '-p ' + args.params

    # third step: refine reads using poisson
    commands += ('ls ' + os.path.join(args.output,'reads.*.fasta') + ' | ' +
                    'parallel -P ' + str(args.threads) + ' poisson consensus {1} {1}.bam ' + 
                    ' . -v ' + paramstr + '\n')
    
    # last step: assemble reads using celera (or other assembler)
    commands += 'poissemble ' + os.path.join(args.output,'corrected.fasta') + '\n'
    
    # save to output dir
    runscript = os.path.join(args.output,'run.sh')
    with open(runscript,'w') as f:
        f.write(commands)
    st = os.stat(runscript)
    os.chmod(runscript, st.st_mode | stat.S_IEXEC)
        
    # and run, if requested
    if not args.write:
        os.system(runscript)
        
        
def consensus(args):

    # load the parameters file first
    params = LoadParams(args.params)

    # open reference sequence and see how many there are
    # (if we gave a file with multiple sequences and no region string, generate one)
    # and we should make sure to split the regions up, if they weren't directly given
    refs = SeqIO.index(args.ref, "fasta")
    if args.regions is None:
        args.regions = []
        for refid in refs:
            # load the reference sequence
            refseq = refs[refid]
            # check if we need to take sub-slices
            if 'max_length' in params and len(refseq) > params['max_length']:
                dl = int(params['max_length']) - 1000
                istart = 0
                iend =int( params['max_length'])
                # step through max_length-1000 at a time (leave nice overlap)
                while istart < iend:
                    args.regions.append('{}:{}:{}'.format(refid,istart,iend))
                    iend = min(iend+dl,len(refseq))
                    istart = min(istart+dl,len(refseq))
            else:
                # or just append the name directly
                args.regions.append(refid)
                
    # now create output file for writing (or stdout)
    if args.output is None:
        fastabase = os.path.splitext(args.ref)[0]
        outfile = open(fastabase+'.corr.fasta','w')
    elif args.output == '-':
        outfile = sys.stdout
    else:
        outfile = open(args.output,'w')
            
    # now loop through and refine sequences
    # (continue on errors)
    for region in args.regions:
        try:
            seq = Mutate(args.ref,args.bam,args.dir,params=params,region=region,
                         test=args.test,verbose=args.verbose)
        except Exception as e:
             sys.stderr.write('Skipping {}: {}\n'.format(region,str(e)))
             continue
                         
        # test mode output returns accuracy as well
        if args.test:
            (seq, acc) = seq
            
        # print corrected fasta string with region as header
        if args.test:
            region += ' [' + str(round(acc,2)) + ']'
            
        outfile.write('>{}\n{}\n'.format(region,seq))
        outfile.flush()
        

        
def variant(args):

    pass
    
    
def train(args):
    
    # now load the specified parameter file
    params = LoadParams(args.params)
    
    # and start the iterations
    for i in range(args.iter):
        # first, generate modified parameter files and save them
        
        # next, use parallel to spin up all of them at once
        
        # finally, pull out the accuracy from the results
        
        # and load corresponding params file
        pass
