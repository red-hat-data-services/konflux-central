#!/bin/bash

git_url=$1
git_commit=$2
BRANCH=$3
dest=$4
src=build

mkdir -p "$dest"
cd $dest
git config --global init.defaultBranch ${BRANCH}
git init
git remote add origin $git_url
git config core.sparseCheckout true
git config core.sparseCheckoutCone false
echo "$src" >> .git/info/sparse-checkout
git fetch --depth=1 origin $git_commit
git checkout $git_commit