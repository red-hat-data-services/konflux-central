#!/bin/bash
epoch=$1

cd stage-release-${epoch}

#Create components snapshot
#oc apply -f snapshot-components

#Start components release
#oc apply -f release-components

#Create all the FBC snapshots for onprem
#oc apply -f snapshot-fbc

#Start all the FBC releases for onprem
#oc apply -f release-fbc

#Start addon FBC release
#oc apply -f release-fbc-addon