#!/usr/bin/env python

# Progressive Cactus Package
# Copyright (C) 2009-2012 by Glenn Hickey (hickey@soe.ucsc.edu)
# and Benedict Paten (benedictpaten@gmail.com)

#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.

#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.

#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#


import os
import sys
import xml.etree.ElementTree as ET
import math
import time
import random
import copy
from optparse import OptionParser
from optparse import OptionGroup
import imp
import string
import socket

from toil.lib.bioio import system
from sonLib.bioio import absSymPath

from seqFile import SeqFile
from cactus.shared.experimentWrapper import ExperimentWrapper
from cactus.shared.experimentWrapper import DbElemWrapper
from cactus.shared.configWrapper import ConfigWrapper
from cactus.shared.common import cactusRootPath

from cactus.progressive.cactus_createMultiCactusProject import runCreateMultiCactusProject


# Wrap up the cactus_progressive interface:
# - intialize the working directory
# - create Experiment file from seqfile and options
# - create Config file from options
# - run cactus_createMultiCactusProject
# - now ready to launch cactus progressive
class ProjectWrapper:
    alignmentDirName = 'progressiveAlignment'
    def __init__(self, options):
        self.options = options
        self.seqFile = SeqFile(options.seqFile)
        self.workingDir = options.cactusDir
        self.configWrapper = None
        self.expWrapper = None
        self.processConfig()
        self.processExperiment()

    def processConfig(self):
        # read in the default right out of cactus
        if self.options.configFile is not None:
            configPath = self.options.configFile
        else:
            dir = cactusRootPath()
            configPath = os.path.join(dir,
                                      "cactus_progressive_config.xml")
        configXml = ET.parse(configPath).getroot()
        self.configWrapper = ConfigWrapper(configXml)
        # here we can go through the options and apply some to the config
        self.configWrapper.setBuildHal(True)
        self.configWrapper.setBuildFasta(True)
        # this is a little hack to effectively toggle back to the
        # non-progressive version of cactus (as published in Gen. Res. 2011)
        # from the high-level interface. 
        if self.options.legacy is True:
            self.configWrapper.setSubtreeSize(sys.maxint)

    def processExperiment(self):
        expXml = self.seqFile.toXMLElement()
        #create the cactus disk
        cdElem = ET.SubElement(expXml, "cactus_disk")
        database = self.options.database
        assert database == "redis" or database == "tokyo_cabinet" or database=="redis"
        confElem = ET.SubElement(cdElem, "st_kv_database_conf")
        confElem.attrib["type"] = database
        dbElem = ET.SubElement(confElem, database)
        self.expWrapper = ExperimentWrapper(expXml)

        if self.options.database == "redis":
            self.expWrapper.setDbPort(str(self.options.Port))
            if self.options.Host is not None:
                self.expWrapper.setDbHost(self.options.Host)
            if self.options.Type == 'memory':
                self.expWrapper.setDbInMemory(True)
                self.expWrapper.setDbSnapshot(False)
            elif self.options.Type == 'snapshot':
                self.expWrapper.setDbInMemory(True)
                self.expWrapper.setDbSnapshot(True)
            else:
                assert self.options.Type == 'disk'
                self.expWrapper.setDbInMemory(False)
                self.expWrapper.setDbSnapshot(False)
            # sonlib doesn't allow for spaces in attributes in the db conf
            # which renders this options useless
            # if self.options.Opts is not None:
            #    self.expWrapper.setDbDBServerOptions(self.options.Opts)
            if self.options.CreateTuning is not None:
                self.expWrapper.setDbCreateTuningOptions(
                    self.options.CreateTuning)
            if self.options.OpenTuning is not None:
                self.expWrapper.setDbReadTuningOptions(
                    self.options.OpenTuning)
        
        #set the sequence output directory
        outSeqDir = os.path.join(self.workingDir, "sequenceData")
        if os.path.exists(outSeqDir) and self.options.overwrite:
            system("rm -rf %s" % outSeqDir)
        if not os.path.exists(outSeqDir):
            os.makedirs(outSeqDir)
        self.expWrapper.setOutputSequenceDir(os.path.join(self.workingDir, 
                                                          "sequenceData"))

    def writeXml(self):
        assert os.path.isdir(self.workingDir)
        configPath = absSymPath(
            os.path.join(self.workingDir, "config.xml"))
        expPath = absSymPath(
            os.path.join(self.workingDir, "expTemplate.xml"))
        self.expWrapper.setConfigPath(configPath)
        self.configWrapper.writeXML(configPath)
        self.expWrapper.writeXML(expPath)

        projPath = os.path.join(self.workingDir,
                                ProjectWrapper.alignmentDirName)
        if os.path.exists(projPath) and self.options.overwrite:
            system("rm -rf %s" % projPath)
        if self.options.outputMaf is True:
            fixNames=1
        else:
            fixNames=0
        if os.path.exists(projPath):
           logPath = os.path.join(self.workingDir, 'cactus.log')
           logFile = open(logPath, "a")
           logFile.write("\nContinuing existing alignment.  Use "
                         "--overwrite or erase the working directory to "
                         "force restart from scratch.\n")
           logFile.close()
        else:
            if len(self.seqFile.outgroups) == 0:
                # No outgroups specified, assume the default outgroup set
                outgroups = None
            else:
                outgroups = self.seqFile.outgroups
            runCreateMultiCactusProject(expPath, projPath, fixNames=fixNames,
                    outgroupNames=outgroups,
                    rootOutgroupDists=self.options.rootOutgroupDists,
                    rootOutgroupPaths=self.options.rootOutgroupPaths,
                    root=self.options.root)

    # create a project in a dummy directory.  check if the
    # project xml is the same as the current project.
    # we do this to see if we should start fresh or try to
    # work with the existing project when the overwrite flag is off
    def isSameAsExisting(self, expPath, projPath, fixNames):
        if not os.path.exists(projPath):
            return False
        oldPath = os.path.dirname(projPath + "/")
        tempPath = "%s_temp" % oldPath
        if os.path.exists(tempPath):
            system("rm -rf %s" % tempPath)
        cmd = "cactus_createMultiCactusProject.py %s %s --fixNames=%d" % (
            expPath, tempPath, fixNames)
        if len(self.seqFile.outgroups) > 0: 
            cmd += " --outgroupNames " + ",".join(self.seqFile.outgroups)
        if self.options.rootOutgroupDists:
            cmd += " --rootOutgroupDists %s" % self.options.rootOutgroupDists
            cmd += " --rootOutgroupPaths %s" % self.options.rootOutgroupPaths
        if self.options.root is not None:
            cmd += " --root %s" % self.options.root
        system(cmd)
        projFilePathNew = os.path.join(tempPath,'%s_temp_project.xml' %
                                       self.alignmentDirName)
        projFilePathOld = os.path.join(oldPath, '%s_project.xml' %
                                       self.alignmentDirName)
        
        newFile = [line for line in open(projFilePathNew, "r")]
        oldFile = [line for line in open(projFilePathOld, "r")]
        areSame = True
        if len(newFile) != len(oldFile):
            areSame = False
        for newLine, oldLine in zip(newFile, oldFile):
            if newLine.replace(tempPath, oldPath) != oldLine:
                areSame = False
        system("rm -rf %s" % tempPath)
        return areSame

    

        
        
