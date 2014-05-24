#!/usr/bin/env python
# File created on 09 Feb 2010
from __future__ import division
from time import time
from os import getcwd, makedirs
from os.path import join, split, abspath

from brokit.formatdb import FormatDb
from skbio.util.misc import remove_files
from skbio.app.parameters import FilePath

from qiime.util import get_qiime_temp_dir
from qiime.exclude_seqs_by_blast import (check_options,
                                         find_homologs,
                                         format_options_as_lines, 
                                         ids_from_fasta_lines,
                                         ids_to_seq_file,
                                         compose_logfile_lines)

__author__ = "Jesse Zaneveld"
__copyright__ = "Copyright 2011, The QIIME Project"
__credits__ = [
    "Jesse Zaneveld",
    "Rob Knight",
    "Justin Kuczynski",
    "Jose Antonio Navas Molina",
    "Adam Robbins-Pianka"]
__license__ = "GPL"
__version__ = "1.8.0-dev"
__maintainer__ = "Jesse Zaneveld"
__email__ = "zaneveld@gmail.com"


from qiime.util import parse_command_line_parameters
from qiime.util import make_option


script_info = {}
script_info[
    'brief_description'] = """Exclude contaminated sequences using BLAST"""
script_info['script_description'] = """

This code is designed to allow users of the QIIME workflow to conveniently exclude unwanted sequences from their data. This is mostly useful for excluding human sequences from runs to comply with Internal Review Board (IRB) requirements, but may also have other uses (e.g. perhaps excluding a major bacterial contaminant). Sequences from a run are searched against a user-specified subject database, where BLAST hits are screened by e-value and the percentage of the query that aligns to the sequence.

For human screening THINK CAREFULLY about the data set that you screen against. Are you excluding human non-coding sequences? What about mitochondrial sequences? This point is CRITICAL because submitting human sequences that are not IRB-approved is BAD.

(e.g. you would NOT want to just screen against just the coding sequences of the human genome as found in the KEGG .nuc files, for example)

One valid approach is to screen all putative 16S rRNA sequences against greengenes to ensure they are bacterial rather than human.

WARNING: You cannot use this script if there are spaces in the path to the database of fasta files because formatdb cannot handle these paths (this is a limitation of NCBI's tools and we have no control over it).
"""
script_info['script_usage'] = []
script_info['script_usage'].append(
    ("""Examples:""",
     """The following is a simple example, where the user can take a given FASTA file (i.e. resulting FASTA file from pick_rep_set.py) and blast those sequences against a reference FASTA file containing the set of sequences which are considered contaminated:""",
     """%prog -i repr_set_seqs.fasta -d ref_seq_set.fna -o exclude_seqs/"""))
script_info['script_usage'].append(
    ("""""",
     """Alternatively, if the user would like to change the percent of aligned sequence coverage ("-p") or the maximum E-value ("-e"), they can use the following command:""",
     """%prog -i repr_set_seqs.fasta -d ref_seq_set.fna -o exclude_seqs/ -p 0.95 -e 1e-10"""))
script_info['output_description'] = """Four output files are generated based on the supplied outputpath + unique suffixes:

1. "filename_prefix".matching: A FASTA file of sequences that did pass the screen (i.e. matched the database and passed all filters).

2. "filename_prefix".non-matching: A FASTA file of sequences that did not pass the screen.

3. "filename_prefix".raw_blast_results: Contains the raw BLAST results from the screening.

4. "filename_prefix".sequence_exclusion_log: A log file summarizing the options used and results obtained.

In addition, if the --no_clean option is passed, the files generated by formatdb will be kept in the same directory as subjectdb.
"""
script_info['required_options'] = [
    make_option(
        "-i", "--querydb", dest='querydb', default=None, type="existing_filepath",
        help="The path to a FASTA file containing query sequences"),
    make_option(
        "-d", "--subjectdb", dest='subjectdb', default=None, type="existing_filepath",
        help="The path to a FASTA file to BLAST against"),
    make_option(
        "-o", "--outputdir", dest='outputdir', default=None, type="new_dirpath",
        help="The output directory")
]
script_info['optional_options'] = [
    make_option("-e", "--e_value", type='float', dest='e_value',
                default=1e-10,
                help="The e-value cutoff for blast queries [default: %default]."),
    make_option("-p", "--percent_aligned", type='float',
                dest='percent_aligned', default=0.97,
                help="The % alignment cutoff for blast queries [default: %default]."),
    make_option("--no_clean", action='store_true',
                dest='no_clean', default=False,
                help="If set, don't delete files generated by formatdb after running [default: %default]."),
    make_option(
        "--blastmatroot", dest='blastmatroot', default=None, type="existing_dirpath",
        help="Path to a folder containing blast matrices [default: %default]."),
    make_option(
        "--working_dir", dest='working_dir', default=get_qiime_temp_dir(), type="existing_dirpath",
        help="Working dir for BLAST [default: %default]."),
    make_option("-m", "--max_hits", type='int', dest='max_hits',
                default=100,
                help="""Max hits parameter for BLAST. CAUTION: Because filtering on alignment percentage occurs after BLAST, a max hits value of 1 in combination with an alignment percent filter could miss valid contaminants. [default: %default]"""),
    make_option("-w", "--word_size", type='int', dest='wordsize',
                default=28,
                help="Word size to use for BLAST search [default: %default]"),
    make_option(
        "-n", "--no_format_db", dest='no_format_db', action="store_true",
        default=False,
        help="""If this flag is specified, format_db will not be called on the subject database (formatdb will be set to False).  This is  useful if you have already formatted the database and a) it took a very long time or b) you want to run the script in parallel on the pre-formatted database [default: %default]""")
]
script_info['version'] = __version__

FORMAT_BAR =   """------------------------------""" * 2


def main():
    option_parser, options, args = parse_command_line_parameters(**script_info)
    DEBUG = options.verbose
    check_options(option_parser, options)
    start_time = time()
    option_lines = format_options_as_lines(options)
    if DEBUG:
        print FORMAT_BAR
        print "Running with options:"
        for line in sorted(option_lines):
            print line
        print FORMAT_BAR

    # because the blast app controller uses absolute paths, make sure subject
    # db path is fully specified

    subject_db = options.subjectdb
    if not subject_db.startswith('/'):
        subject_db = join(getcwd(), subject_db)
    if not options.no_format_db:

        # initialize object
        inpath = FilePath(abspath(options.subjectdb))
        subject_dir, subj_file = split(inpath)

        fdb = FormatDb(WorkingDir=subject_dir)
        # Currently we do not support protein blasts, but
        # this would be easy to add in the future...
        fdb.Parameters['-p'].on('F')

        # Create indices for record lookup
        fdb.Parameters['-o'].on('T')

        # Set input database
        fdb.Parameters['-i'].on(subject_db)

        formatdb_cmd = fdb.BaseCommand

        if DEBUG:
            print "Formatting db with command: %s" % formatdb_cmd

        app_result = fdb(subject_db)
        formatdb_filepaths = []
        for v in app_result.values():
            try:
                formatdb_filepaths.append(v.name)
            except AttributeError:
                # not a file object, so no path to return
                pass

        db_format_time = time() - start_time

        if DEBUG:
            print "Formatting subject db took: %2.f seconds" % db_format_time
            print "formatdb log file written to: %s" % app_result['log']
            print FORMAT_BAR
    else:
        db_format_time = time() - start_time
        formatdb_cmd = "None (formatdb not called)"
        # Check that User-Supplied subjectdb is valid
        db_ext = [".nhr", ".nin", ".nsd", ".nsi", ".nsq"]
        formatdb_filepaths = [subject_db + ext for ext in db_ext]

        if DEBUG:
            print "Checking that pre-existing formatdb files exist and can be read."
            print "Files to be checked:"
            for fp in formatdb_filepaths:
                print fp
            print FORMAT_BAR

        try:
            formatdb_files = [open(db_f, "U") for db_f in formatdb_filepaths]
            [f.close() for f in formatdb_files]
        except IOError:
            if DEBUG:
                print "Cannot open user-supplied database file:", db_f
            option_parser.error(
                """Problem with -d and --no_format_db option combination: Cannot open the following user-supplied database file: %s. Consider running without --no_format_db to let formatdb generate these required files""" %
                db_f)

        if DEBUG:
            print "OK: BLAST Database files exist and can be read."
            print FORMAT_BAR

    # Perform BLAST search
    blast_results, hit_ids, removed_hit_ids = find_homologs(options.querydb,
                                                            subject_db, options.e_value, options.max_hits,
                                                            options.working_dir, options.blastmatroot, options.wordsize,
                                                            options.percent_aligned, DEBUG=DEBUG)

    blast_time = (time() - start_time) - db_format_time

    if DEBUG:
        print "BLAST search took: %2.f minute(s)" % (blast_time / 60.0)
        print FORMAT_BAR

    # Create output folder
    outputdir = options.outputdir
    try:
        makedirs(outputdir)
    except OSError:
        pass

    # Record raw blast results
    raw_blast_results_path = join(outputdir, "raw_blast_results.txt")
    f = open(raw_blast_results_path, 'w')
    f.writelines(blast_results)
    f.close()

    # Record excluded seqs
    excluded_seqs_path = join(outputdir, "matching.fna")
    ids_to_seq_file(hit_ids, options.querydb, excluded_seqs_path, "")

    # Record included (screened) seqs
    included_seqs_path = join(outputdir, "non-matching.fna")
    all_ids = ids_from_fasta_lines(open(options.querydb))
    included_ids = set(all_ids) - hit_ids
    ids_to_seq_file(included_ids, options.querydb, included_seqs_path, "")

    log_lines = compose_logfile_lines(start_time, db_format_time, blast_time,
                                      option_lines, formatdb_cmd,
                                      blast_results, options, all_ids,
                                      hit_ids, removed_hit_ids,
                                      included_ids, DEBUG)

    log_path = join(outputdir, "sequence_exclusion.log")
    if DEBUG:
        print "Writing summary to: %s" % log_path

    f = open(log_path, 'w')
    f.writelines(log_lines)
    f.close()

    if not options.no_clean:
        if DEBUG:

            print FORMAT_BAR
            print "|                           Cleanup                        |"
            print FORMAT_BAR

        if not options.no_format_db:
            if options.verbose:
                print "Cleaning up formatdb files:", formatdb_filepaths
            remove_files(formatdb_filepaths)
        else:
            if options.verbose:
                print "Formatdb not run...nothing to clean"

if __name__ == "__main__":
    main()
