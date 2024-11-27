#!/bin/bash
epoch=$1

cd release-${epoch}
#oc apply -f snapshot-components-stage-rhoai-v2-16-${epoch}.yaml
#oc apply -f release-components-stage-rhoai-v2-16-${epoch}.yaml

#oc apply -f snapshot-fbc
#oc apply -f release-fbc

#oc apply -f release-fbc-addon