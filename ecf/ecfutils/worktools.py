#! /usr/bin/env python3
f'This python module requires python 3.6 or newer'

import logging, os, io, sys, datetime, glob, shutil, subprocess, re
from collections import OrderedDict
from copy import copy
logger=logging.getLogger('crow.model.fv3gfs')

YAML_DIRS_TO_COPY=[ 'schema', 'workflow' ] # important: no ending /
YAML_FILES_TO_COPY=[ '_main.yaml', 'settings.yaml' ]

try:
    import crow
except ImportError as ie:
    thisdir=os.path.dirname(os.path.abspath(__file__))
    topdir=os.path.realpath(os.path.join(thisdir,"../.."))
    sys.path.append(topdir)
    del thisdir, topdir

level=logging.WARNING
if os.environ.get('WORKTOOLS_VERBOSE','NO') == 'YES':
    level=logging.INFO
logging.basicConfig(stream=sys.stderr,level=level)

import crow.tools, crow.config
from crow.metascheduler import to_ecflow, to_rocoto
from crow.config import from_dir, Suite, from_file, to_yaml
from crow.tools import Clock

ECFNETS_INCLUDE = "/ecf/ecfnets/include"
SIX_HOURS = datetime.timedelta(seconds=6*3600)

def loudly_make_dir_if_missing(dirname):
    if dirname and not os.path.exists(dirname):
        logger.info(f'{dirname}: make directory')
        os.makedirs(dirname)

def loudly_make_symlink(src,tgt):
    logger.debug(f'{src}: symlink {tgt}')
    with suppress(FileNotFoundError): os.unlink(tgt)
    if not os.path.exists(src):
        logger.warning(f'{src}: link target does not exist')
    os.symlink(src,tgt)

def make_parent_dir(filename):
    loudly_make_dir_if_missing(os.path.dirname(filename))

def create_COMROT(conf):
    cdump = conf.case.IC_CDUMP
    icsdir = conf.case.IC_DIR
    comrot = conf.places.ROTDIR
    resens = conf.fv3_enkf_settings.CASE[1:]
    resdet = conf.fv3_gfs_settings.CASE[1:]
    idate = conf.case.SDATE
    detdir = f'{cdump}.{idate:%Y%m%d}/{idate:%H}'
    nens = conf.data_assimilation.NMEM_ENKF
    enkfdir = f'enkf.{cdump}.{idate:%Y%m%d}/{idate:%H}'
    idatestr = f'{idate:%Y%m%d%H}'

    logger.info(f'Input conditions: {icsdir}')

    loudly_make_dir_if_missing(os.path.join(comrot,enkfdir))
    loudly_make_dir_if_missing(os.path.join(comrot, detdir))

    logger.info(f'Workflow COM root: {comrot}')

    # Link ensemble member initial conditions
    for i in range(1, nens + 1):
        memdir=os.path.join(comrot,enkfdir,f'mem{i:03d}')
        loudly_make_dir_if_missing(memdir)
        src=os.path.join(icsdir, idatestr, f'C{resens}',f'mem{i:03d}','INPUT')
        tgt=os.path.join(comrot, enkfdir, f'mem{i:03d}', 'INPUT')
        loudly_make_symlink(src,tgt)

    # Link deterministic initial conditions
    src=os.path.join(icsdir, idatestr, f'C{resdet}', 'control', 'INPUT')
    tgt=os.path.join(comrot, detdir, 'INPUT')
    loudly_make_symlink(src,tgt)

    # Link bias correction and radiance diagnostics files
    for fname in ['abias', 'abias_pc', 'abias_air', 'radstat']:
        file=f'{cdump}.t{idate:%H}z.{fname}'
        src=os.path.join(icsdir, idatestr, file)
        tgt=os.path.join(comrot, detdir, file)
        loudly_make_symlink(src,tgt)

def find_case_yaml_file_for(case_name):
    for case_file in [ case_name,f"{case_name}.yaml",f"cases/{case_name}",
                       f"cases/{case_name}.yaml","/" ]:
        if os.path.exists(case_file) and case_file!='/':
            logger.info(f"{case_file}: file for this case")
            break
    if case_file == "/":
        epicfail(f"{case_name}: no such case; pick one from in cases/")
    if not os.path.exists("user.yaml"):
        epicfail("Please copy user.yaml.default to user.yaml and fill in values.")
    with io.StringIO() as yfd:
        follow_main(yfd,".",{ "case_yaml":case_file, "user_yaml":"user.yaml" })
        yaml=yfd.getvalue()
    return crow.config.from_string(yaml)

def read_yaml_suite(dir):
    logger.info(f'{dir}: read yaml files specified in _main.yaml')
    conf=from_dir(dir)
    crow.config.validate(conf.settings)
    suite=Suite(conf.suite)
    return conf,suite

def make_yaml_files_in_expdir(srcdir,tgtdir):
    if not os.path.exists(tgtdir):
        logger.info(f'{tgtdir}: make directory')
        os.makedirs(tgtdir)
    logger.info(f'{tgtdir}: send yaml files to here')
    logger.info(f'{srcdir}: get yaml files from here')
    for srcfile in YAML_DIRS_TO_COPY + YAML_FILES_TO_COPY:
        srcbase=os.path.basename(srcfile)
        tgtfile=os.path.join(tgtdir,srcbase)
        if os.path.isdir(srcfile):
            logger.info(f'{srcbase}: copy yaml directory tree')
            if os.path.exists(tgtfile):
                shutil.rmtree(tgtfile)
            shutil.copytree(srcfile,tgtfile)
        else:
            logger.info(f'{srcbase}: copy yaml file')
            shutil.copyfile(srcfile,tgtfile)
        del srcbase,tgtfile

    readme=[ os.path.join(srcdir,'schema/settings.yaml') ]

    # Deal with the static files:
    for srcfile in glob.glob(f'{srcdir}/static/*.yaml'):
        logger.info(f'{srcfile}: read file')
        doc=from_file(srcfile)
        tgtfile=os.path.join(tgtdir,"static_"+os.path.basename(srcfile))
        yaml=to_yaml(doc)
        logger.info(f'{tgtfile}: generate file')
        with open(tgtfile,'wt') as fd:
            fd.write('# This file is automatically generated from:\n')
            fd.write(f'#    {srcfile}')
            fd.write('# Changes to this file may be overwritten.\n\n')
            fd.write(yaml)
        readme.insert(0,tgtfile)
        del doc,tgtfile

    # Read the settings file
    readme.append('settings.yaml')
    logger.info(f'Read files: {", ".join(readme)}')
    doc=from_file(*readme)
    
    # Now the resources:
    resource_basename=doc.settings.resource_file
    resource_srcfile=os.path.join(srcdir,resource_basename)
    resource_tgtfile=os.path.join(tgtdir,'resources.yaml')
    logger.info(f'{resource_srcfile}: use this resource yaml file')
    shutil.copyfile(resource_srcfile,resource_tgtfile)
    logger.info(f'{tgtdir}: yaml files created here')

def make_clocks_for_cycle_range(suite,first_cycle,last_cycle,surrounding_cycles):
    suite_clock=copy(suite.Clock)
    logger.info(f'cycles to write:   {first_cycle:%Ft%T} - {last_cycle:%Ft%T}')
    suite.ecFlow.write_cycles = Clock(
        start=first_cycle,end=last_cycle,step=SIX_HOURS)
    first_analyzed=max(suite_clock.start,first_cycle-surrounding_cycles*SIX_HOURS)
    last_analyzed=min(suite_clock.end,last_cycle+surrounding_cycles*SIX_HOURS)
    logger.info(f'cycles to analyze: {first_analyzed:%Ft%T} - {last_analyzed:%Ft%T}')
    suite.ecFlow.analyze_cycles=Clock(
        start=first_analyzed,end=last_analyzed,step=SIX_HOURS)

def generate_ecflow_suite_in_memory(suite,first_cycle,last_cycle,surrounding_cycles):
    logger.info(f'make suite for cycles: {first_cycle:%Ft%T} - {last_cycle:%Ft%T}')
    make_clocks_for_cycle_range(suite,first_cycle,last_cycle,surrounding_cycles)
    suite_defs, ecf_files = to_ecflow(suite)
    return suite_defs, ecf_files

def write_ecflow_suite_to_disk(targetdir, suite_defs, ecf_files):
    written_suite_defs=OrderedDict()
    logger.info(f'{targetdir}: write suite here')
    for deffile in suite_defs.keys():
        defname = suite_defs[deffile]['name']
        defcontents = suite_defs[deffile]['def']
        filename=os.path.realpath(os.path.join(targetdir,'defs',deffile))
        make_parent_dir(filename)
        logger.info(f'{defname}: {filename}: write suite definition')
        with open(os.path.join(targetdir,filename),'wt') as fd:
            fd.write(defcontents)
        written_suite_defs[defname]=filename
        for setname in ecf_files:
            logger.info(f'{defname}: write ecf file set {setname}')
            for filename in ecf_files[setname]:
                full_fn=os.path.realpath(os.path.join(targetdir,defname,filename)+'.ecf')
                logger.debug(f'{defname}: {setname}: write ecf file {full_fn}')
                make_parent_dir(full_fn)
                with open(full_fn,'wt') as fd:
                    fd.write(ecf_files[setname][filename])
    return written_suite_defs

def get_target_dir_and_check_ecflow_env():
    ECF_HOME=os.environ.get('ECF_HOME',None)

    if not ECF_HOME:
        logger.error('Set $ECF_HOME to location where your ecflow files should reside.')
        return None
    elif not os.environ.get('ECF_PORT',None):
        logger.error('Set $ECF_PORT to the port number of your ecflow server.')
        return None
    elif not os.path.isdir(ECF_HOME):
        logger.error('Directory $ECF_HOME={ECF_HOME} does not exist.  You need to set up your account for ecflow before you can run any ecflow workflows.')
        return None
    
    for file in [ 'head.h', 'tail.h', 'envir-xc40.h' ]:
        yourfile=os.path.join(ECF_HOME,file)
        if not os.path.exists(yourfile):
            logger.warning(f'{yourfile}: does not exist.  I will get one for you.')
            os.symlink(os.path.join(ECFNETS_INCLUDE,file),yourfile)
        else:
            logger.info(f'{yourfile}: exists.')
        
    return ECF_HOME

def create_new_ecflow_workflow(suite,surrounding_cycles=1):
    ECF_HOME=get_target_dir_and_check_ecflow_env()
    if not ECF_HOME: return None,None,None,None
    first_cycle=suite.Clock.start
    last_cycle=min(suite.Clock.end,first_cycle+suite.Clock.step*2)
    suite_defs, ecf_files = generate_ecflow_suite_in_memory(
        suite,first_cycle,last_cycle,surrounding_cycles)
    suite_def_files = write_ecflow_suite_to_disk(
        ECF_HOME,suite_defs,ecf_files)
    return ECF_HOME, suite_def_files, first_cycle, last_cycle

def update_existing_ecflow_workflow(suite,first_cycle,last_cycle,
                                    surrounding_cycles=1):
    ECF_HOME=get_target_dir_and_check_ecflow_env()
    suite_defs, ecf_files = generate_ecflow_suite_in_memory(
        suite,first_cycle,last_cycle,surrounding_cycles)
    suite_def_files = write_ecflow_suite_to_disk(
        ECF_HOME,suite_defs,ecf_files)
    return ECF_HOME, suite_def_files

def load_ecflow_suites(ECF_HOME,suite_def_files):
    logger.info(f'{ECF_HOME}: load suites: '
                f'{", ".join(suite_def_files.keys())}')
    with crow.tools.chdir(ECF_HOME):
        for file in suite_def_files.values():
            cmd=f'ecflow_client --load {file}'
            logger.info(cmd)
            subprocess.run(cmd,check=False,shell=True)

def begin_ecflow_suites(ECF_HOME,suite_def_files):
    logger.info(f'{ECF_HOME}: begin suites: '
                f'{", ".join(suite_def_files.keys())}')
    with crow.tools.chdir(ECF_HOME):
        for suite in suite_def_files.keys():
            cmd=f'ecflow_client --begin {suite}'
            logger.info(cmd)
            subprocess.run(cmd,check=False,shell=True)

def make_rocoto_xml(suite,filename):
    with open(filename,'wt') as fd:
        logger.info(f'{filename}: create Rocoto XML document')
        fd.write(to_rocoto(suite))
    print(f'{filename}: Rocoto XML document created here.')
    
########################################################################

# These functions are called directly from scripts, and can be thought
# of as "main programs."

def remake_ecflow_files_for_cycles(
        yamldir,first_cycle_str,last_cycle_str,
        surrounding_cycles=1):
    ECF_HOME=get_target_dir_and_check_ecflow_env()
    conf,suite=read_yaml_suite(yamldir)
    loudly_make_dir_if_missing(f'{conf.settings.COM}/log')

    first_cycle=datetime.datetime.strptime(first_cycle_str,'%Y%m%d%H')
    first_cycle=max(suite.Clock.start,first_cycle)

    last_cycle=datetime.datetime.strptime(last_cycle_str,'%Y%m%d%H')
    last_cycle=max(first_cycle,min(suite.Clock.end,last_cycle))

    suite_defs, ecf_files = generate_ecflow_suite_in_memory(
        suite,first_cycle,last_cycle,surrounding_cycles)
    written_suite_defs = write_ecflow_suite_to_disk(
        ECF_HOME, suite_defs, ecf_files)
    print(f'''Suite definition files and ecf files have been written to:

  {ECF_HOME}

If all you wanted to do was update the ecf files, then you're done.

If you want to update the suite (cycle) definitions, or add suites
(cycles), you will need to call ecflow_client's --load, --begin,
--replace, or --delete commands.''')

def create_and_load_ecflow_workflow(yamldir,surrounding_cycles=1,begin=False):
    conf,suite=read_yaml_suite(yamldir)
    loudly_make_dir_if_missing(f'{conf.settings.COM}/log')
    ECF_HOME, suite_def_files, first_cycle, last_cycle = \
        create_new_ecflow_workflow(suite,surrounding_cycles)
    if not ECF_HOME:
        logger.error('Could not create workflow files.  See prior errors for details.')
        return False
    load_ecflow_suites(ECF_HOME,suite_def_files)
    if begin:
        begin_ecflow_suites(ECF_HOME,suite_def_files)
        
def add_cycles_to_running_ecflow_workflow_at(
        yamldir,first_cycle_str,last_cycle_str,surrounding_cycles=1): 
    conf,suite=read_yaml_suite(yamldir)
    first_cycle=datetime.datetime.strptime(first_cycle_str,'%Y%m%d%H')
    last_cycle=datetime.datetime.strptime(last_cycle_str,'%Y%m%d%H')
    ECF_HOME, suite_def_files = update_existing_ecflow_workflow(
        suite,first_cycle,last_cycle,surrounding_cycles)
    load_ecflow_suites(ECF_HOME,suite_def_files)    
    begin_ecflow_suites(ECF_HOME,suite_def_files)    

def make_rocoto_xml_for(yamldir):
    conf,suite=read_yaml_suite(yamldir)
    loudly_make_dir_if_missing(f'{conf.settings.COM}/log')
    make_rocoto_xml(suite,f'{yamldir}/workflow.xml')
