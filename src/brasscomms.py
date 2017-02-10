#! /usr/bin/env python

""" defines and implements the the TA RESTful interface """

### standard imports
from __future__ import with_statement

from os.path import exists, isfile
from os import access, R_OK
## from threading import Lock ## todo: do we need a lock?
import json
import datetime
import requests

### relevant third party imports
from flask import Flask, Response, request

import rospy
import actionlib
import ig_action_msgs.msg
from move_base_msgs.msg import MoveBaseAction

### other brasscomms modules
from constants import (TH_URL, CONFIG_FILE_PATH, LOG_FILE_PATH, CP_GAZ,
                       JSON_MIME, Error, LogError, QUERY_PATH, # Status,
                       START, OBSERVE, SET_BATTERY, PLACE_OBSTACLE,
                       REMOVE_OBSTACLE, PERTURB_SENSOR)
from gazebo_interface import GazeboInterface
# from map_util import *
from parse import Coords, Bump, Config, TestAction, Voltage, ObstacleID, SingleBumpName

### some definitions and helper functions

def done_cb(terminal, result):
    """ callback for when the bot is at the target """
    log_das(LogError.INFO, "brasscomms received successful result from plan: %d" % terminal)

def active_cb():
    """ callback for when the bot is made active """
    log_das(LogError.INFO, "brasscoms received notification that goal is active")

### some globals
app = Flask(__name__)
deadline = datetime.datetime.now() ## this is a default value; the result
                                   ## of observe will be well formed but
                                   ## wrong unless they call start first

## shared_var_lock = Lock() ## todo :commented out until we have occasion to use it

def parse_config_file():
    """ checks the appropriate place for the config file, and loads into an object if possible """
    if exists(CONFIG_FILE_PATH) and isfile(CONFIG_FILE_PATH) and access(CONFIG_FILE_PATH, R_OK):
        with open(CONFIG_FILE_PATH) as config_file:
            data = json.load(config_file)
            conf = Config(**data)
            return conf
    else:
        # todo: does sending this this sufficiently stop the world if the file doesn't parse?
        # todo: return something?
        th_das_error(Error.TEST_DATA_FILE_ERROR,
                     '%s does not exist, is not a file, or is not readable' % CONFIG_FILE_PATH)

### subroutines for forming API results
def th_error():
    """ the response for a TH error """
    return Response(status=400)

def action_result(body):
    """ given a body, produce the action result with headers """
    return Response(json.dumps({"TIME" : (datetime.datetime.now()).isoformat(),
                                "RESULT": body}),
                    status=200, mimetype=JSON_MIME)

### subroutines for forming and sending messages to the TH
def th_das_error(err, msg):
    """ posts a DAS_ERROR formed with the arguments """
    dest = TH_URL + "/error"
    now = datetime.datetime.now()
    error_contents = {"TIME" : now.isoformat(),
                      "ERROR" : err.name,
                      "MESSAGE" : msg}
    try:
        requests.post(dest, data=json.dumps(error_contents))
    except Exception as e:
        log_das(LogError.STARTUP_ERROR, "Fatal: cannot connect to TH at %s: %s" % (dest, e))

def log_das(error, msg):
    """ formats the arguments per the API and inserts them to the log """
    try:
        with open(LOG_FILE_PATH, 'a') as log_file:
            error_contents = {"TIME" : (datetime.datetime.now()).isoformat(),
                              "TYPE" : error.name,
                              "MESSAGE" : msg}
            data = json.dumps(error_contents)
            log_file.write(data + "\n")
    except StandardError as e:
        th_das_error(Error.DAS_LOG_FILE_ERROR, '%s could not be accessed: %s' % (LOG_FILE_PATH, e))

def das_ready():
    """ POSTs DAS_READY to the TH, or logs if failed"""
    dest = TH_URL + "/ready"
    contents = {"TIME" : (datetime.datetime.now()).isoformat()}
    try:
        requests.post(dest, data=json.dumps(contents))
    except Exception as e:
        log_das(LogError.STARTUP_ERROR, "Fatal: couldn't connect to TH at %s: %s" % (dest, e))

def check_action(req, path, methods):
    """ return true if the request respects the methods, false and log it otherwise """

    # check that the method is running from actually getting hit
    if req.path != path:
        log_das(LogError.RUNTIME_ERROR, 'internal fault: %s called improperly' % path)
        return False

    # check that it's being called in a way it's designed for
    if not request.method in methods:
        log_das(LogError.RUNTIME_ERROR,
                '%s called with bad HTTP request: %s not in %s' % (path, req.method, methods))
        return False

    # if it's a post, make sure that it got JSON. req.method also needs to
    # be in methods, but that must be true from above
    if (req.method == 'POST') and (request.headers['Content-Type'] != JSON_MIME):
        log_das(LogError.RUNTIME_ERROR, '%s POSTed to without json header' % path)
        return False

    return True

def instruct(ext):
    """ given an extension, provides the path to the config-relevant file in instructions """
    global config

    return CP_GAZ + '/instructions/' + config.start_loc + '_to_' + config.target_loc + ext

### subroutines per endpoint URL in API wiki page order
@app.route(QUERY_PATH.url, methods=QUERY_PATH.methods)
def action_query_path():
    """ implements query path end point """
    if not check_action(request, QUERY_PATH.url, QUERY_PATH.methods):
        return th_error()

    try:
        with open(instruct('.json')) as path_file:
            data = json.load(path_file)
            return action_result({'path' : data['path']})
    except Exception as e:
        log_das(LogError.RUNTIME_ERROR,
                "error in reading the files for %s: %s " % (QUERY_PATH.url, e))
        return th_error()

@app.route(START.url, methods=START.methods)
def action_start():
    """ implements start end point """
    if not check_action(request, START.url, START.methods):
        return th_error()

    try:
        j = request.get_json(silent=True)
        params = TestAction(**j)
    except Exception as e:
        log_das(LogError.RUNTIME_ERROR,
                '%s got a malformed test action POST: %s' % (START.url, e))
        return th_error()

    global deadline

    log_das(LogError.INFO, "starting challenge problem")
    try:
        ## todo: test and make sure this change didn't break anything
        with open(instruct('.ig')) as igfile:
            igcode = igfile.read()
            goal = ig_action_msgs.msg.InstructionGraphGoal(order=igcode)
            global client
            client.send_goal(goal=goal, done_cb=done_cb, active_cb=active_cb)

        # update the deadline to be now + the amount of time for the path
        # given in the json file
        with open(instruct('.json')) as config_file:
            data = json.load(config_file)
            deadline = datetime.datetime.now() + datetime.timedelta(seconds=data['time'])
    except Exception as e:
        log_das(LogError.RUNTIME_ERROR, "could not send the goal in %s: %s " % (START.url, e))
        ## todo: should we th_error() here? this seems bad.

    return action_result({})  # todo: this includes time as well; is that
                              # out of spec?

@app.route(OBSERVE.url, methods=OBSERVE.methods)
def action_observe():
    """ implements observe end point """
    if not check_action(request, OBSERVE.url, OBSERVE.methods):
        return th_error()

    global gazebo
    global deadline

    try:
        x, y, w, vel = gazebo.get_turtlebot_state()
        observation = {"x" : x, "y" : y, "w" : w,
                       "v" : vel,
                       "voltage" : -1,  # todo: Need to work this out
                       "deadline" : deadline.isoformat()
                      }
        return action_result(observation)
    except Exception as e:
        log_das(LogError.RUNTIME_ERROR, "error in %s: %s " % (OBSERVE.url, e))
        return th_error()

@app.route(SET_BATTERY.url, methods=SET_BATTERY.methods)
def action_set_battery():
    """ implements set_battery end point """
    if not check_action(request, SET_BATTERY.url, SET_BATTERY.methods):
        return th_error()

    try:
        j = request.get_json(silent=True)
        params = TestAction(**j)
        params.ARGUMENTS = Voltage(**params.ARGUMENTS)
    except Exception as e:
        log_das(LogError.RUNTIME_ERROR,
                '%s got a malformed test action POST: %s' % (SET_BATTERY.url, e))
        return th_error()

    ## todo : implement real stuff here when we have the battery
    ## model. also need to check that the argument voltage is less than the
    ## current voltage, not just a valid possible voltage?

    return action_result({})

@app.route(PLACE_OBSTACLE.url, methods=PLACE_OBSTACLE.methods)
def action_place_obstacle():
    """ implements place_obstacle end point """
    if not check_action(request, PLACE_OBSTACLE.url, PLACE_OBSTACLE.methods):
        return th_error()

    try:
        j = request.get_json(silent=True)
        params = TestAction(**j)
        params.ARGUMENTS = Coords(**params.ARGUMENTS)
    except Exception as e:
        log_das(LogError.RUNTIME_ERROR,
                '%s got a malformed test action POST: %s' % (PLACE_OBSTACLE.url, e))
        return th_error()

    global gazebo

    obs_name = gazebo.place_new_obstacle(params.ARGUMENTS.x, params.ARGUMENTS.y)
    if obs_name is not None:
        return action_result({"obstacleid" : obs_name})
    else:
        log_das(LogError.RUNTIME_ERROR, 'gazebo cant place new obstacle at given x y')
        return th_error()

@app.route(REMOVE_OBSTACLE.url, methods=REMOVE_OBSTACLE.methods)
def action_remove_obstacle():
    """ implements remove_obstacle end point """
    if not check_action(request, REMOVE_OBSTACLE.url, methods=REMOVE_OBSTACLE.methods):
        return th_error()

    try:
        j = request.get_json(silent=True)
        params = TestAction(**j)
        params.ARGUMENTS = ObstacleID(**params.ARGUMENTS)
    except Exception as e:
        log_das(LogError.RUNTIME_ERROR,
                '%s got a malformed test action POST: %s' % (REMOVE_OBSTACLE.url, e))
        return th_error()

    global gazebo
    print "-----------------------"
    print str(params)
    print "-----------------------"
    success = gazebo.delete_obstacle(params.ARGUMENTS.obstacleid)
    if success:
        return action_result({})
    else:
        log_das(LogError.RUNTIME_ERROR, 'action/remove_obstacle gazebo call failed')
        return th_error()

@app.route(PERTURB_SENSOR.url, methods=PERTURB_SENSOR.methods)
def action_perturb_sensor():
    """ implements perturb_sensor end point """
    if not check_action(request, PERTURB_SENSOR.url, methods=PERTURB_SENSOR.methods):
        return th_error()

    try:
        j = request.get_json(silent=True)
        params = TestAction(**j)
        params.ARGUMENTS = SingleBumpName(**params.ARGUMENTS)
        params.ARGUMENTS.bump = Bump(**params.ARGUMENTS.bump)
    except Exception as e:
        log_das(LogError.RUNTIME_ERROR,
                '%s got a malformed test action POST: %s' % (PERTURB_SENSOR.url, e))
        return th_error()

    ## todo: currently we have no sensor to bump, so this doesn't do
    ## anything other than check the format of the request and reply with
    ## something well-formatted if it gets something well-formatted
    return action_result({})

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
    client = actionlib.SimpleActionClient("ig_action_server",
                                          ig_action_msgs.msg.InstructionGraphAction)
    client.wait_for_server()

    # make an interface into Gazebo
    gazebo = GazeboInterface()

    # parse the config file
    try:
        config = parse_config_file()
    except Exception as e:
        log_das(LogError.STARTUP_ERROR, "Fatal: config file doesn't parse: %s" % e)

    # this should block until the navigation stack is ready to recieve goals
    move_base = actionlib.SimpleActionClient("move_base", MoveBaseAction)
    move_base.wait_for_server()

    ## todo: call bradley's stuff to teleport the robot to the place
    ## it's actully starting not l1

    ## todo: this posts errors to the TH, but we should stop the world when that happens

    ## todo: this may happen too early
    das_ready()

    ## actually start up the flask service. this never returns, so it must
    ## be the last thing in the file
    app.run(host="0.0.0.0")
