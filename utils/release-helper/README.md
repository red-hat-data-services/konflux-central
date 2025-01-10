RHOAI-Release-Helper
====================

Objectives
----------
* To prepare all the required artifacts for stage and prod release
* To validate the consistency of all the artifacts
* To ensure the correct sequence of release process through automated flow

Pros
------
* To avoid manual efforts during the stage/prod release process
* To eliminate any possible errors with the manual work
* To avoid inconsistency with any artifacts


Setup & Prerequisites
------
* Make sure you have “yq” installed
* Create a file “~/.ssh/.quay_devops_application_token” with the contents as the secret value from of the quay application token
* Clone https://github.com/rhoai-rhtap/RHOAI-Konflux-Automation to your machine