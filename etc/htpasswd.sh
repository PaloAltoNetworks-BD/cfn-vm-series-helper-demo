#!/bin/bash

printf "vmsh:$(openssl passwd -apr1 $1)\n" >> $2
