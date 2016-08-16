#!/usr/bin/env python
# ----------------------------------------------------------------------
# Numenta Platform for Intelligent Computing (NuPIC)
# Copyright (C) 2015-2016, Numenta, Inc.  Unless you have an agreement
# with Numenta, Inc., for a separate license for this software code, the
# following terms and conditions apply:
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero Public License version 3 as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See the GNU Affero Public License for more details.
#
# You should have received a copy of the GNU Affero Public License
# along with this program.  If not, see http://www.gnu.org/licenses.
#
# http://numenta.org/licenses/
# ----------------------------------------------------------------------

import copy
import csv
import json
import os
import random

from pkg_resources import resource_filename

from nupic.data.file_record_stream import FileRecordStream
from nupic.engine import Network
from nupic.encoders import MultiEncoder, ScalarEncoder, DateEncoder
from nupic.regions.SPRegion import SPRegion
from nupic.regions.TPRegion import TPRegion


""" A Network API demo for scalar value predicition with hotgym. Uses
    record sensor, spatial pooler, temporal pooler, and SDR Classifier
    regions to get a scalar value prediction using hot gym data.
"""


_VERBOSITY = 0  # how chatty the demo should be
_SEED = 1956  # the random seed used throughout in the regions
_INPUT_FILE_PATH = resource_filename(
  "nupic.datafiles", "extra/hotgym/rec-center-hourly.csv"
)
_OUTPUT_PATH = "network-demo-prediction-output.csv"
_NUM_RECORDS = 2000

# Config field for SPRegion
SP_PARAMS = {
  "spVerbosity": _VERBOSITY,
  "spatialImp": "cpp",
  "globalInhibition": 1,
  "columnCount": 2048,
  # This must be set before creating the SPRegion
  "inputWidth": 0,
  "numActiveColumnsPerInhArea": 40,
  "seed": 1956,
  "potentialPct": 0.8,
  "synPermConnected": 0.1,
  "synPermActiveInc": 0.0001,
  "synPermInactiveDec": 0.0005,
  "maxBoost": 1.0,
}

# Config field for TPRegion
TP_PARAMS = {
  "verbosity": _VERBOSITY,
  "columnCount": 2048,
  "cellsPerColumn": 32,
  "inputWidth": 2048,
  "seed": 1960,
  "temporalImp": "cpp",
  "newSynapseCount": 20,
  "maxSynapsesPerSegment": 32,
  "maxSegmentsPerCell": 128,
  "initialPerm": 0.21,
  "permanenceInc": 0.1,
  "permanenceDec": 0.1,
  "globalDecay": 0.0,
  "maxAge": 0,
  "minThreshold": 9,
  "activationThreshold": 12,
  "outputType": "normal",
  "pamLength": 3,
}

# Params for the SDR Classifier. Tuning alpha properly will drastically
# affect performance for different use cases.
CL_PARAMS = {  
  'alpha': 0.005, # Learning rate. Higher values make it adapt faster
  'steps': '1', # Comma separated list of # of steps to predict into the future
  'implementation': 'py', # The specific implementation of the classifier to use
  'verbosity': _VERBOSITY, # Verbosity level, [0 (silent) - 6 (loud)]
}



def createEncoder():
  """Create the encoder instance for our test and return it."""
  consumption_encoder = ScalarEncoder(21, 0.0, 100.0, n=50, name="consumption",
      clipInput=True)
  time_encoder = DateEncoder(timeOfDay=(21, 9.5), name="timestamp_timeOfDay")

  encoder = MultiEncoder()
  encoder.addEncoder("consumption", consumption_encoder)
  encoder.addEncoder("timestamp", time_encoder)

  return encoder



def createNetwork(dataSource):
  """Create the Network instance.

  The network has a sensor region reading data from `dataSource` and passing
  the encoded representation to an SPRegion. The SPRegion output is passed to
  a TPRegion.

  :param dataSource: a RecordStream instance to get data from
  :returns: a Network instance ready to run
  """
  network = Network()

  # Our input is sensor data from the gym file. The RecordSensor region
  # allows us to specify a file record stream as the input source via the
  # dataSource attribute.
  network.addRegion("sensor", "py.RecordSensor",
                    json.dumps({"verbosity": _VERBOSITY}))
  sensor = network.regions["sensor"].getSelf()
  # The RecordSensor needs to know how to encode the input values
  sensor.encoder = createEncoder()
  # Specify the dataSource as a file record stream instance
  sensor.dataSource = dataSource

  # Create the spatial pooler region
  SP_PARAMS["inputWidth"] = sensor.encoder.getWidth()
  spatialPoolerRegion = network.addRegion("spatialPoolerRegion",
                                          "py.SPRegion",
                                          json.dumps(SP_PARAMS))

  # Link the SP region to the sensor input
  network.link("sensor", "spatialPoolerRegion", "UniformLink", "")
  network.link("sensor", "spatialPoolerRegion", "UniformLink", "",
               srcOutput="resetOut", destInput="resetIn")
  network.link("spatialPoolerRegion", "sensor", "UniformLink", "",
               srcOutput="spatialTopDownOut", destInput="spatialTopDownIn")
  network.link("spatialPoolerRegion", "sensor", "UniformLink", "",
               srcOutput="temporalTopDownOut", destInput="temporalTopDownIn")

  # Make sure learning is enabled
  spatialPoolerRegion.setParameter("learningMode", True)
  # We want temporal anomalies so disable anomalyMode in the SP. This mode is
  # used for computing anomalies in a non-temporal model.
  spatialPoolerRegion.setParameter("anomalyMode", False)


  # Add the TPRegion on top of the SPRegion
  temporalPoolerRegion = network.addRegion("temporalPoolerRegion",
                                           "py.TPRegion",
                                           json.dumps(TP_PARAMS))

  network.link("spatialPoolerRegion", "temporalPoolerRegion", "UniformLink", "")
  network.link("temporalPoolerRegion", "spatialPoolerRegion", "UniformLink", "",
               srcOutput="topDownOut", destInput="topDownIn")

  # Enable topDownMode to get the predicted columns output
  temporalPoolerRegion.setParameter("topDownMode", True)
  # Make sure learning is enabled (this is the default)
  temporalPoolerRegion.setParameter("learningMode", True)
  # Enable inference mode so we get predictions
  temporalPoolerRegion.setParameter("inferenceMode", True)
  # Enable anomalyMode to compute the anomaly score to be passed to the anomaly
  # likelihood region. 
  temporalPoolerRegion.setParameter("anomalyMode", True)

  classifier = network.addRegion("SDRClassifierRegion",
                                 "py.SDRClassifierRegion",
                                 json.dumps(CL_PARAMS))

  network.link("temporalPoolerRegion", "SDRClassifierRegion", "UniformLink", "",
               srcOutput="bottomUpOut", destInput="bottomUpIn")
  network.link("sensor", "SDRClassifierRegion", "UniformLink", "",
               srcOutput="sourceOut", destInput="categoryIn")
  network.link("temporalPoolerRegion", "SDRClassifierRegion", "UniformLink", "",
               srcOutput="predictedActiveCells",
               destInput="predictedActiveCells")
  network.link("sensor", "SDRClassifierRegion", "UniformLink", "",
               srcOutput="sequenceIdOut", destInput="sequenceIdIn")

  classifier.setParameter('inferenceMode', True)
  classifier.setParameter('learningMode', True)

  return network


def runNetwork(network, writer):
  """Run the network and write output to writer.

  :param network: a Network instance to run
  :param writer: a csv.writer instance to write output to
  """
  sensorRegion = network.regions["sensor"]
  spatialPoolerRegion = network.regions["spatialPoolerRegion"]
  temporalPoolerRegion = network.regions["temporalPoolerRegion"]
  classiferRegion = network.regions["SDRClassifierRegion"]

  prevPredictedColumns = []

  for i in xrange(_NUM_RECORDS):
    # Run the network for a single iteration
    network.run(1)

    # Write out the predicted value along with the record number and consumption
    # value.
    consumption = sensorRegion.getOutputData("sourceOut")[0]
    actualValues = classiferRegion.getOutputData("actualValues").tolist()
    probabilities = classiferRegion.getOutputData("probabilities").tolist()
    
    pMax = max(probabilities)
    epsilon = .00001 # epsilon for floating point comparison
    candidates = [actualValues[i] for i in xrange(len(actualValues))
                  if (probabilities[i] - pMax) < epsilon]

    prediction = candidates[random.randint(0, len(candidates) - 1)]
    
    writer.writerow((i, consumption, prediction))


if __name__ == "__main__":
  dataSource = FileRecordStream(streamID=_INPUT_FILE_PATH)

  network = createNetwork(dataSource)
  network.initialize()

  spRegion = network.getRegionsByType(SPRegion)[0]
  sp = spRegion.getSelf().getAlgorithmInstance()
  print "spatial pooler region inputs: {0}".format(spRegion.getInputNames())
  print "spatial pooler region outputs: {0}".format(spRegion.getOutputNames())
  print "# spatial pooler columns: {0}".format(sp.getNumColumns())
  print

  tmRegion = network.getRegionsByType(TPRegion)[0]
  tm = tmRegion.getSelf().getAlgorithmInstance()
  print "temporal memory region inputs: {0}".format(tmRegion.getInputNames())
  print "temporal memory region outputs: {0}".format(tmRegion.getOutputNames())
  print "# temporal memory columns: {0}".format(tm.numberOfCols)
  print

  outputPath = os.path.join(os.path.dirname(__file__), _OUTPUT_PATH)
  with open(outputPath, "w") as outputFile:
    writer = csv.writer(outputFile)
    print "Writing output to %s" % outputPath
    runNetwork(network, writer)
