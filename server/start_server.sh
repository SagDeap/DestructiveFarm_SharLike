#!/bin/sh

# Use FLASK_DEBUG=True if needed

FLASK_APP=$(dirname $(readlink -f $0))/standalone.py python3 -m flask run --host 127.0.0.1 --with-threads
