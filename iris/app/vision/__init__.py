"""What the robot sees.

``robot_eye`` talks to the ESP32-CAM, finds and recognises faces on this
machine, and hands frames to a vision model when the question is about
things rather than people. It imports nothing heavy at module scope, so IRIS
boots with no vision library installed; the tools in
``iris/app/tools/devices/camera.py`` translate its errors into speech.
"""
