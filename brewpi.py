# Copyright 2012 BrewPi/Elco Jacobs.
# This file is part of BrewPi.

# BrewPi is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# BrewPi is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with BrewPi.  If not, see <http://www.gnu.org/licenses/>.
from pprint import pprint
import serial
import time
from datetime import datetime
# import gviz_api
import socket
import sys
import os
import shutil
import urllib
import simplejson as json
from configobj import ConfigObj

#local imports
import thread
import temperatureProfile
import programArduino as programmer
import brewpiJson
import Queue

# use simulator in arduino
# from simulator import Simulator

# Settings will be read from Arduino, initialize with same defaults as Arduino
# This is mainly to show what's expected. Will all be overwritten on the first update from the arduino

compatibleBrewpiVersion = "0.1.0"

# Control Settings
cs = {	'mode': 'b',
		'beerSetting': 20.0,
		'fridgeSetting': 20.0,
		'heatEstimator': 0.2,
		'coolEstimator': 5}

# Control Constants
cc = {	"tempFormat":"C",
		"tempSetMin": 1.0,
		"tempSetMax": 30.0,
		"Kp": 20.000,
		"Ki": 0.600,
		"Kd":-3.000,
		"iMaxErr": 0.500,
		"idleRangeH": 1.000,
		"idleRangeL":-1.000,
		"heatTargetH": 0.301,
		"heatTargetL":-0.199,
		"coolTargetH": 0.199,
		"coolTargetL":-0.301,
		"maxHeatTimeForEst":"600",
		"maxCoolTimeForEst":"1200",
		"fridgeFastFilt":"1",
		"fridgeSlowFilt":"4",
		"fridgeSlopeFilt":"3",
		"beerFastFilt":"3",
		"beerSlowFilt":"5",
		"beerSlopeFilt":"4"}

# Control variables
cv = {	"beerDiff": 0.000,
		"diffIntegral": 0.000,
		"beerSlope": 0.000,
		"p": 0.000,
		"i": 0.000,
		"d": 0.000,
		"estPeak": 0.000,
		"negPeakEst": 0.000,
		"posPeakEst": 0.000,
		"negPeak": 0.000,
		"posPeak": 0.000}

lcdText = ['Script starting up',' ',' ',' ']

# Read in command line arguments
if len(sys.argv) < 2:
	sys.exit('Usage: %s <config file full path>' % sys.argv[0])
if not os.path.exists(sys.argv[1]):
	sys.exit('ERROR: Config file "%s" was not found!' % sys.argv[1])

configFile = sys.argv[1]

# global variables, will be initialized by startBeer()
config = ConfigObj(configFile)

def configValue(key, defValue):
	return config.get(key, defValue)

isSimulator = config.get("simulator", False)

localJsonFileName = ""
localCsvFileName = ""
wwwJsonFileName = ""
wwwCsvFileName = ""
lastDay = ""
day = ""

scriptStart = time.time()
secondsSinceStart = 0

# default is 2 days
exitAfter = float(configValue("runFor", 2))*60*60*24

# simulator = Simulator()

# wwwSettings.json is a copy of some of the settings for the web server
def changeWwwSetting(settingName, value):
	wwwSettingsFile = open(config['wwwPath'] + 'wwwSettings.json', 'r+b')
	wwwSettings = json.load(wwwSettingsFile)
	wwwSettings[settingName] = value
	wwwSettingsFile.seek(0)
	wwwSettingsFile.write(json.dumps(wwwSettings))
	wwwSettingsFile.truncate()
	wwwSettingsFile.close()

conn = None

def updateProfileTemp():
	return temperatureProfile.getNewTemp(config['scriptPath'], secondsSinceStart)


def fetchProfile():
	global conn
	# use urllib to download the profile as a CSV file
	profileUrl = ("https://spreadsheets.google.com/tq?key=" +
		config['profileKey'] +
		"&tq=select D,E&tqx=out:csv")  # select the right cells and CSV format
	profileFileName = config['scriptPath'] + 'settings/tempProfile.csv'
	if os.path.isfile(profileFileName + '.old'):
		os.remove(profileFileName + '.old')
	if os.path.isfile(profileFileName):
		os.rename(profileFileName, profileFileName + '.old')
	urllib.urlretrieve(profileUrl, profileFileName)
	if not conn is None:
		if os.path.isfile(profileFileName):
			conn.send("Profile successfuly updated")
		else:
			conn.send("Failed to update profile")
	temperatureProfile.flushProfile()
	updateProfileTemp()

def startBeer(beerName):
	global config
	global localJsonFileName
	global localCsvFileName
	global wwwJsonFileName
	global wwwCsvFileName
	global lastDay
	global day

	# create directory for the data if it does not exist
	dataPath = config['scriptPath'] + 'data/' + beerName + '/'
	wwwDataPath = config['wwwPath'] + 'data/' + beerName + '/'

	if not os.path.exists(dataPath):
		os.makedirs(dataPath)
		os.chmod(dataPath, 0775)  # give group all permissions
	if not os.path.exists(wwwDataPath):
		os.makedirs(wwwDataPath)
		os.chmod(wwwDataPath, 0775)  # sudgive group all permissions

	# Keep track of day and make new data tabe for each day
	# This limits data table size, which can grow very big otherwise
	day = time.strftime("%Y-%m-%d")
	lastDay = day
	# define a JSON file to store the data table
	jsonFileName = config['beerName'] + '-' + day
	#if a file for today already existed, add suffix
	if not isSimulator and os.path.isfile(dataPath + jsonFileName + '.json'):
		i = 1
		while(os.path.isfile(
				dataPath + jsonFileName + '-' + str(i) + '.json')):
			i = i + 1
		jsonFileName = jsonFileName + '-' + str(i)
	localJsonFileName = dataPath + jsonFileName + '.json'
	brewpiJson.newEmptyFile(localJsonFileName)

	# Define a location on the webserver to copy the file to after it is written
	wwwJsonFileName = wwwDataPath + jsonFileName + '.json'

	# Define a CSV file to store the data as CSV (might be useful one day)
	localCsvFileName = (dataPath + config['beerName'] + '.csv')
	wwwCsvFileName = (wwwDataPath + config['beerName'] + '.csv')
	changeWwwSetting('beerName', beerName)

	fetchProfile()


def logMessage(message):
	print >> sys.stderr, time.strftime("%b %d %Y %H:%M:%S   ") + message



ser = None
# open serial port
try:
	ser = serial.Serial(config['port'], 57600, timeout=1)
except serial.SerialException, e:
	print e
	exit()

logMessage("Notification: Script started for beer '" + config['beerName'] + "'")
# wait for 10 seconds to allow an Uno to reboot (in case an Uno is being used)
time.sleep(float(config.get('startupDelay', 10)))

ser.flush()
retries = 0
while(1):  # read all lines on serial interface
	line = ser.readline()
	if(line):  # line available?
		if line[0] == 'N':
			parts = line[2:].strip('\n').split(':')
			brewpiVersion = parts[0]
			# todo - fetch simulator flag from version line
			if brewpiVersion == compatibleBrewpiVersion:
				print "Found BrewPi version " + brewpiVersion
			else:
				logMessage("Warning: BrewPi version compatible with this script is " + 
					compatibleBrewpiVersion + 
					" but version number received is " + brewpiVersion)

			if len(parts)>1:
				isSimulator = parts[1]="simulate"
			if isSimulator:
				logMessage("Running simulator.")
			break
	else:
		ser.write('n')
		time.sleep(1)
		retries += 1
		if retries > 5:
			print ("Warning: Cannot receive version number from Arduino. " + 
				"Script might not be compatible.")
			break

ser.flush()

# send initial config to the arduino. The changes will be read back here. This is mainly used for
# setting up the mode.
if isSimulator:
	initialJson = configValue("initialJson", None)
	if initialJson:
		ser.write("j"+initialJson)

# request settings from Arduino, processed later when reply is received
ser.write('s') # request control settings cs
ser.write('c') # request control constants cc

simulatorConfig = configValue("simulatorStart", "i:30,r:-1")
ser.write('u'+simulatorConfig) # update every 30 secs  run at full speed

# answer from Arduino is received asynchronously later.

#create a listening socket to communicate with PHP
is_windows = sys.platform.startswith('win')
useInetSocket = bool(config.get('useInetSocket', is_windows));
if (useInetSocket):
	s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
	s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
	s.bind((config.get('socketHost', 'localhost'), int(config.get('socketPort', 6332))))
else:
	if os.path.exists(config['scriptPath'] + 'BEERSOCKET'):
	# if socket already exists, remove it
		os.remove(config['scriptPath'] + 'BEERSOCKET')
	s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
	s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
	s.bind(config['scriptPath'] + 'BEERSOCKET')  # Bind BEERSOCKET
	# set all permissions for socket
	os.chmod(config['scriptPath'] + 'BEERSOCKET', 0777)
s.setblocking(1)  # set socket functions to be blocking
s.listen(50)  # Create a backlog queue for up to 5 connections
# blocking socket functions wait 'serialCheckInterval' seconds
s.settimeout(float(config.get('socketTimeout', 0.01)))

# prevDataTime = 0.0  # keep track of time between new data requests
prevDataTime = time.time()
prevTimeOut = time.time()

run = 1

startBeer(config['beerName'])

prevSimulatorStep = time.time()
prevProfileTime = 0

# simulator.step()        # take a first step

webConnectionQueue = Queue.Queue()
controllerRequestQueue = Queue.Queue()
outputTemperature = False


# I had contemplated putting this in the profile, but it's really part of the equipment.
def fetchSimulatorConfigJSON():
	sim = {}
	configNames = [""]

def handleWebConnections():
	while (run):
		try:
			conn, addr = s.accept()
			# blocking receive, times out in serialCheckInterval
			webConnectionQueue.put(conn)
		except socket.timeout:
			pass
	s.close()

def handleControllerRequests():
	while (run):
		line = ser.readline()
		if (line):
			controllerRequestQueue.put(line)

def fetch(queue):
	try:
		return queue.get_nowait()
	except Queue.Empty:
		return None

prevTempJson = {
	"BeerTemp":0,
    "FridgeTemp":0,
    "BeerAnn":None,
    "FridgeAnn":None,
    "RoomTemp":None,
    "State":None,
    "BeerSet":0,
    "FridgeSet":0
}


def renameTempKey(key):
	rename = {
		"bt" : "BeerTemp",
	    "bs" : "BeerSet",
	    "ba":"BeerAnn",
	    "ft":"FridgeTemp",
	    "fs":"FridgeSet",
	    "fa":"FridgeAnn",
	    "rt":"RoomTemp",
	    "s":"State",
	    "t":"Time"
	}
	return rename.get(key, key)

def tempFormat(t):
	return "{0:.2f}".format(t)


webConnectionThread = thread.start_new_thread(handleWebConnections, ())
controllerRequestThread = thread.start_new_thread(handleControllerRequests, ())


while(run):
	# Check wheter it is a new day
	lastDay = day
	day = time.strftime("%Y-%m-%d")
	if lastDay != day:
		logMessage("Notification: New day, dropping data table and creating new JSON file.")
		jsonFileName = config['beerName'] + '/' + config['beerName'] + '-' + day
		localJsonFileName = config['scriptPath'] + 'data/' + jsonFileName + '.json'
		wwwJsonFileName = config['wwwPath'] + 'data/' + jsonFileName + '.json'
		# create new empty json file
		brewpiJson.newEmptyFile(localJsonFileName)



	# Wait for incoming socket connections.
	# When nothing is received, socket.timeout will be raised after
	# serialCheckInterval seconds. Serial receive will be done then.
	# When messages are expected on serial, the timeout is raised 'manually'
	expectedResponse = False
	try:
		'''
		t = time.time()
		simulator.step()
		simulatorTime = int(simulator.time)
		msg = "u{s:="+str(simulatorTime)+",b:"+tempFormat(simulator.outputBeerTemp())+",f:"+tempFormat(simulator.outputFridgeTemp())+"}\n"
		print msg
		ser.write(msg)
		#expectedResponse = True
		#prevSimulatorStep += 1.0
		'''
		conn = webConnectionQueue.get_nowait()
		message = conn.recv(1024)
		conn.close()
		if "=" in message:
			messageType, value = message.split("=", 1)
		else:
			messageType = message
		if messageType == "ack":  # acknowledge request
			conn.send('ack')
		elif messageType == "lcd":  # lcd contents requested
			conn.send(json.dumps(lcdText))
		elif messageType == "getMode":  # echo cs['mode'] setting
			conn.send(cs['mode'])
		elif messageType == "getFridge":  # echo fridge temperature setting
			conn.send(str(cs['fridgeSet']))
		elif messageType == "getBeer":  # echo fridge temperature setting
			conn.send(str(cs['beerSet']))
		elif messageType == "getControlConstants":
			conn.send(json.dumps(cc))
		elif messageType == "getControlSettings":
			conn.send(json.dumps(cs))
		elif messageType == "getControlVariables":
			conn.send(json.dumps(cv))
		elif messageType == "refreshControlConstants":
			ser.write("c")
			raise socket.timeout
		elif messageType == "refreshControlSettings":
			ser.write("s")
			raise socket.timeout
		elif messageType == "refreshControlVariables":
			ser.write("v")
			raise socket.timeout
		elif messageType == "loadDefaultControlSettings":
			ser.write("S")
			raise socket.timeout
		elif messageType == "loadDefaultControlConstants":
			ser.write("C")
			raise socket.timeout
		elif messageType == "setBeer":  # new constant beer temperature received
			newTemp = float(value)
			if(newTemp > cc['tempSetMin'] and newTemp < cc['tempSetMax']):
				cs['mode'] = 'b'
				# round to 2 dec, python will otherwise produce 6.999999999
				cs['beerSet'] = round(newTemp, 2)
				ser.write("j{mode:b, beerSet:" + str(cs['beerSet']) + "}")
				logMessage(	"Notification: Beer temperature set to " +
							str(cs['beerSet']) +
							" degrees in web interface")
				raise socket.timeout  # go to serial communication to update Arduino
			else:
				logMessage("Beer temperature setting" + str(newTemp) +
							" is outside allowed range " +
							str(cc['tempSetMin']) + "-" + str(cc['tempSetMax']))
		elif messageType == "setFridge":  # new constant fridge temperature received
			newTemp = float(value)
			if(newTemp > cc['tempSetMin'] and newTemp < cc['tempSetMax']):
				cs['mode'] = 'f'
				cs['fridgeSet'] = round(newTemp, 2)
				ser.write("j{mode:f, fridgeSet:" + str(cs['fridgeSet']) + "+")
				logMessage("Notification: Fridge temperature set to " +
							str(cs['fridgeSet']) +
							" degrees in web interface")
				raise socket.timeout  # go to serial communication to update Arduino
		elif messageType == "setProfile":  # cs['mode'] set to profile
			# read temperatures from currentprofile.csv
			cs['mode'] = 'p'
			cs['beerSet'] = updateProfileTemp()
			ser.write("j{mode:p, beerSet:" + str(cs['beerSet']) + "}")
			logMessage("Notification: Profile mode enabled")
			raise socket.timeout  # go to serial communication to update Arduino
		elif messageType == "setOff":  # cs['mode'] set to OFF
			cs['mode'] = 'o'
			ser.write("j{mode:o}")
			logMessage("Notification: Temperature control disabled")
			raise socket.timeout
		elif messageType == "setParameters":
			# receive JSON key:value pairs to set parameters on the Arduino
			try:
				decoded = json.loads(value)
				ser.write("j" + json.dumps(decoded))
			except json.JSONDecodeError:
				logMessage("Error: invalid json string received: " + value)
			raise socket.timeout
		elif messageType == "stopScript":  # exit instruction received. Stop script.
			run = 0
			# voluntary shutdown.
			# write a file to prevent the cron job from restarting the script
			dontrunfile = open(config['wwwPath'] + 'do_not_run_brewpi', "w")
			dontrunfile.write("1")
			dontrunfile.close()
			continue
		elif messageType == "interval":  # new interval received
			newInterval = int(value)
			if(newInterval > 5 and newInterval < 5000):
				config['interval'] = float(newInterval)
				config.write()
				logMessage("Notification: Interval changed to " +
							str(newInterval) + " seconds")
		elif messageType == "name":  # new beer name
			newName = value
			if(len(newName) > 3):	 # shorter names are probably invalid
				config['beerName'] = newName
				startBeer(newName)
				config.write()
				logMessage("Notification: restarted for beer: " + newName)
		elif messageType == "profileKey":
			config['profileKey'] = value
			config.write()
			changeWwwSetting('profileKey', value)
		elif messageType == "uploadProfile":
			fetchProfile()
		elif messageType == "programArduino":
			ser.close  # close serial port before programming
			del ser  # Arduino won't reset when serial port is not completely removed
			programParameters = json.loads(value)
			hexFile = programParameters['fileName']
			boardType = programParameters['boardType']
			port = config['port']
			eraseEEPROM = programParameters['eraseEEPROM']
			logMessage("New program uploaded to Arduino, script will restart")
			result = programmer.programArduino(config, boardType, hexFile, port, eraseEEPROM)

			# avrdudeResult = programmer.programArduino(	programParameters['boardType'],
			#							programParameters['fileName'],
			#							config['port'],
			#							programParameters['eraseEEPROM'])
			conn.send(result)
			# restart the script when done. This replaces this process with the new one
			time.sleep(5)  # give the Arduino time to reboot
			python = sys.executable
			os.execl(python, python, * sys.argv)
		else:
			logMessage("Error: Received invalid message on socket: " + message)

		if (False and (time.time() - prevTimeOut) < config['serialCheckInterval']):
			continue
		else:
			# raise exception to check serial for data immediately
			raise socket.timeout

	except (socket.timeout, Queue.Empty):
		# Do serial communication and update settings every SerialCheckInterval

		t = time.time()
		periodicUpdate = ((t-prevTimeOut)>5.0)
		if periodicUpdate:
			# request new LCD text
			ser.write('l')

			# request Settings from Arduino to stay up to date
			ser.write('s')
			outputTemperature = True
			prevTimeOut = t

		# if no new data has been received for serialRequestInteval seconds
		# if((time.time() - prevDataTime) >= float(config['interval'])):
		#	ser.write("t")  # request new from arduino

		if((t - prevDataTime) > float(config['interval']) +
										2 * float(config['interval'])):
			#something is wrong: arduino is not responding to data requests
			logMessage("Error: Arduino is not responding to new data requests")

		lineRead = False
		while(not lineRead):  # read all lines on serial interface
			line = fetch(controllerRequestQueue)
			if(line):  # line available?
				try:
					lineRead = True
					if(line[0] == 'T'):
						# print it to stdout
						if outputTemperature:
							print time.strftime("%b %d %Y %H:%M:%S  ") + line[2:]

						# process temperature line
						newData = json.loads(line[2:])

						# copy/rename keys
						for key in newData:
							prevTempJson[renameTempKey(key)] = newData[key]

						newRow = prevTempJson

						# write complete datatable to json file

						seconds = int(newRow['Time'])
						secondsSinceStart = seconds
						run = (secondsSinceStart<exitAfter)

						newRow['Time'] = scriptStart+seconds

						brewpiJson.addRow(localJsonFileName, newRow)

						#write csv file too
						csvFile = open(localCsvFileName, "a")

						lineToWrite = (time.strftime("%b %d %Y %H:%M:%S;", time.localtime(scriptStart+seconds)) +
							str(newRow['BeerTemp']) + ';' +
							str(newRow['BeerSet']) + ';' +
							str(newRow['BeerAnn']) + ';' +
							str(newRow['FridgeTemp']) + ';' +
							str(newRow['FridgeSet']) + ';' +
							str(newRow['FridgeAnn']) + ';' +
							str(newRow['State']) + '\n')

						csvFile.write(lineToWrite)
						csvFile.close()

						# my this is inefficient
						if outputTemperature:
							shutil.copyfile(localCsvFileName, wwwCsvFileName)
							# copy to www dir.
							# Do not write directly to www dir to prevent blocking www file.
							shutil.copyfile(localJsonFileName, wwwJsonFileName)

						state = int(newRow['State'])
						#simulator.setMode(state)

						# store time of last new data for interval check
						prevDataTime = time.time()
						outputTemperature = False
					elif(line[0] == 'D'):
						# debug message received
						logMessage("Arduino debug message: " + line[2:])
					elif(line[0] == 'L'):
						# lcd content received
						lcdTextReplaced = line[2:].replace('\xb0','&deg') #replace degree sign with &deg
						lcdText = json.loads(lcdTextReplaced)
					elif(line[0] == 'C'):
						# Control constants received
						cc = json.loads(line[2:])
						logMessage("Control constants received: ")
						pprint(cc, sys.stderr)

					elif(line[0] == 'S'):
						# Control settings received
						cs = json.loads(line[2:])
						# do not print this to the log file. This is requested continuously.
					elif(line[0] == 'V'):
						# Control settings received
						cv = json.loads(line[2:])
						logMessage("Control variables received: ")
						pprint(cv, sys.stderr)
					elif(line[0] == 'N'):
						pass # version number received. Do nothing, just ignore
					else:
						logMessage("Cannot process line from Arduino: " + line)
					# end or processing a line
				except json.decoder.JSONDecodeError, e:
					logMessage("JSON decode error: %s" % e)
			else:
				if (lineRead):
					break

		# Check for update from temperature profile
		if cs['mode'] == 'p' and (secondsSinceStart-prevProfileTime)>10:
			prevProfileTime = secondsSinceStart
			newTemp = int(updateProfileTemp()*100)/float(100.0)    # round to 2 places
			if newTemp > cc['tempSetMin'] and newTemp < cc['tempSetMax']:
				if newTemp != cs['beerSet']:
					# if temperature has to be updated send settings to arduino
					cs['beerSet'] = newTemp
					ser.write("j{beerSet:" + str(cs['beerSet']) + "}")

	except socket.error, e:
		logMessage("socket error: %s" % e)

run = 0

ser.close()  # close port
