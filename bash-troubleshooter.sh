#!/usr/bin/bash
# TODO: make the below also do firewall checks etc
ss -tulpn | grep "8889"
ss -tulpn | grep "8890"
ss -tulpn | grep "11111"