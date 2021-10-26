#!/bin/bash
uuid=$(cat manifest.uuid | cut -c1-12)
filename="./polls/static/release_${uuid}.tar.gz"
./fossil tarball $uuid $filename
