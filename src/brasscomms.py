#! /usr/bin/env python

### imports
from __future__ import with_statement
import roslib
import rospy
import actionlib
import ig_action_msgs.msg
import sys
import tf

from threading import Lock

import datetime

from flask import Flask , request , abort
from enum import Enum

import requests
import json
import os.path
import os.access

### some definitions and helper functions
class Status(Enum):
    PERTURBATION_DETECTED  = 1
    MISSION_SUSPENDED = 2
    MISSION_RESUMED = 3
    MISSION_HALTED = 4
    MISSION_ABORTED = 5
    ADAPTATION_INITIATED = 6
    ADAPTATION_COMPLETED = 7
    ADAPTATION_STOPPED = 8
    ERROR = 9

class Error(Enum):
    TEST_DATA_FILE_ERROR  = 1
    TEST_DATA_FORMAT_ERROR = 2
    DAS_LOG_URI_ERROR = 3
    DAS_OTHER_ERROR = 4

def isint(x):
    try:
        int(s)
        return True
    except ValueError:
        return False

def isbool(x):
    if (x == 'true' or x == 'false'):
        return True
    return False

# returns true iff the first argument is a digit inclusively between the
# second two args. assumes that the second two are indeed digits, and that
# the second is less than the third.
def int_out_of_range(x,upper,lower) :
    return not(isint(x) and x >= lower and x <= upper)

## callbacks to change the status
def done_cb(terminal, result):
    global bot_status
    bot_status = Status.ADAPTATION_COMPLETED # todo: right enum?
    print "brasscomms received successful result from plan: %d" %(terminal)

def active_cb():
    global bot_status
    bot_status = Status.Operational #todo: what to say here?
    print "brasscoms received notification that goal is active"

### some globals
app = Flask(__name__)
shared_var_lock = Lock ()

# todo: this could be a horrible concurrency bug; i don't know yet.
start_percentage = -1      # default value
bot_status = Status.ERROR  # default value

th_url = "http://brass-th"

## todo
def parse_config_file():
    config_file_path = '/test/data'
    # check if file exists, and can be read, TEST_DATA_FILE_ERROR DAS_ERROR otw
    # parse into dict, TEST_DATA_FORMAT_ERROR DAS_ERROR otw

    if not (os.path.exists(config_file_path)
            and os.path.isfile(config_file_path)
            and os.access(config_file_path,os.R_OK)):
        th_das_error(TEST_DATA_FILE_ERROR,'config file either does not exist, is not a file, or has wrong permissions')

    with open(config_file_path) as config_file:
        data = json.load(config_file)

    # check to make sure each field is as in the spec ..

    # start_loc
    # start_yaw
    # target_loc
    # enable_adaptation
    # initial_voltage
    # initial_obstacle
    # initial_obstacle_location
    # sensor_perturbation

    return data

### subroutines for forming API results
def formActionResult(arguments):
    now = datetime.datetime.now()
    ACTION_RESULT = {TIME : now.isoformat (),
		     ARGUMENTS: arguments}
    return ACTION_RESULT

def th_error():
    return Response(status=400)

def action_result(body):
    return Response(flask.jsonify(**body),status=200, mimetype='application/json')

### subroutines for forming and sending messages to the TH
def th_das_error(err,msg):
    global th_url
    now = datetime.datetime.now()
    error_contents = {TIME : now.isoformat (),
                      ERROR : str(err),
                      MESSAGE : str(msg)}
    r = requests.post(th_url+'/error', data = json.dumps(error_contents))

def das_ready():
    global th_url
    now = datetime.datetime.now()
    contents = {TIME : now.isoformat (),
    r = requests.post(th_url+'/ready', data = json.dumps(contents))

### subroutines per endpoint URL in API wiki page order

@app.route('/action/start', methods=['POST'])
def action_start():
    if(request.path != '/action/start' or request.method != 'POST'):
        th_das_error(DAS_OTHER_ERROR,'internal fault: action_start called improperly')

    print "starting challenge problem"
    try:
        # todo: pick ig based on start and end point, rather than hard coded (RR2)
        igfile = open('/home/vagrant/catkin_ws/src/cp1_gazebo/instructions/newnav.ig', "r")
        igcode = igfile.read()
        goal = ig_action_msgs.msg.InstructionGraphGoal(order=igcode)
        global client
        client.send_goal( goal = goal, done_cb = done_cb, active_cb = active_cb)
    except Exception as e:
        ## todo: here post the error to the relevant location in brass-th
        print e
        print "Could not send the goal!"

    return 'starting challenge problem' ## todo

@app.route('/action/observe', methods=['GET'])
def action_observe(arg):
    if(request.path != '/action/observe' or request.method != 'GET'):
        th_das_error(DAS_OTHER_ERROR,'internal fault: action_observe called improperly')

    global gazebo

    try:
    	x, y, w = gazebo.get_turtlebot_state()
	observation = {x : x, y : y, w : w,
		       v : -1,      # How to calculate velocity
                       voltage: -1  # Need to work this out
		      }
	return action_result(observation)
    except:
	return th_error()

@app.route('/action/set_battery', methods=['POST'])
def action_set_battery(arg):
    if(request.path != '/action/set_battery' or request.method != 'POST'):
        th_das_error(DAS_OTHER_ERROR,'internal fault: action_set_battery called improperly')

    return "this is a stub"

@app.route('/action/place_obstacle', methods=['POST'])
def action_place_obstacle(arg):
    if(request.path != '/action/place_obstacle' or asser request.method != 'POST'):
        th_das_error(DAS_OTHER_ERROR,'internal fault: action_place_obstacle called improperly')

    if(request.headers['Content-Type'] != "application/json"):
        th_das_error(DAS_OTHER_ERROR,'action/place_obstacle recieved post without json header')

    params = request.get_json(silent=True)
    assert 'x' in params.keys()
    assert 'y' in params.keys()
    global gazebo

    obs_name = gazebo.place_new_obstacle(params[x], params[y])
    if obs_name is not None:
	ARGUMENTS = {obstacle_id : obs_name};
        ACTION_RESULT = createActionResult(ARGUMENTS)
        return action_result(ACTION_RESULT)
    else:
	return th_error()

@app.route('/action/remove_obstacle', methods=['POST'])
def action_remove_obstacle(arg):
    if(request.path != '/action/remove_obstacle' or  request.method != 'POST'):
        th_das_error(DAS_OTHER_ERROR,'internal fault: action_observe called improperly')

    if( request.headers['Content-Type'] != "application/json"):
        th_das_error(DAS_OTHER_ERROR,'action/remove_obstacle recieved post without json header')

    params = request.get_json(silent=True)
    assert 'obstacle_id' in params.keys()

    obstacle_id = params[obstacle_id]

    global gazebo
    success = gazebo.delete_obstacle(obstacle_id)
    if success:
	ACTION_RESULT = createActionResult({})
	return action_result(ACTION_RESULT)
    else:
	return th_error()



# if you run this script from the command line directly, this causes it to
# actually launch the little web server and the node
#
# the host parameter above make the server visible externally to any
# machine on the network, rather than just this one. in the context of
# the simulator, this combined with configured port-forwarding in the
# Vagrant file means that you can run curl commands against the guest
# machine from the host. for debugging, this may be unsafe depending
# on your machine configuration and network attachements.
if __name__ == "__main__":
    ## start up the ros node and make an action server
    rospy.init_node("brasscomms")
    client = actionlib.SimpleActionClient("ig_action_server", ig_action_msgs.msg.InstructionGraphAction)
    client.wait_for_server()
    gazebo = GazeboInterface()
    app.run (host="0.0.0.0")
    parse_config_file() ## todo: if this fails, will the whole thing stop?
    das_ready()
