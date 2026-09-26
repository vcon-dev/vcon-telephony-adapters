-- recording_webhook.lua
-- FreeSWITCH Lua script to POST webhook on recording complete
-- Place in /opt/homebrew/etc/freeswitch/scripts/

local api = freeapi:execute
local uuid = session:getVariable("uuid")
local caller_id_number = session:getVariable("caller_id_number") or ""
local caller_id_name = session:getVariable("caller_id_name") or ""
local destination_number = session:getVariable("destination_number") or ""
local direction = session:getVariable("direction") or "inbound"
local start_epoch = session:getVariable("start_epoch") or ""
local duration = session:getVariable("duration") or "0"
local record_seconds = session:getVariable("record_seconds") or "0"
local recording_file = session:getVariable("recording_file") or ""
local context = session:getVariable("context") or "default"
local accountcode = session:getVariable("accountcode") or ""

-- Build JSON payload
local json_payload = string.format(
    '{"uuid":"%s","caller_id_number":"%s","caller_id_name":"%s",'..
    '"destination_number":"%s","direction":"%s","start_epoch":"%s",'..
    '"duration":"%s","record_seconds":"%s","recording_file":"%s",'..
    '"context":"%s","accountcode":"%s"}',
    uuid, caller_id_number, caller_id_name,
    destination_number, direction, start_epoch,
    duration, record_seconds, recording_file,
    context, accountcode
)

-- POST to telephony adapter webhook
local webhook_url = "http://localhost:8080/webhook/recording"
freeswitch.consoleLog("INFO", "Posting recording webhook for " .. uuid .. " to " .. webhook_url .. "\n")

-- Use curl via api
local cmd = string.format(
    'system curl -s -X POST "%s" -H "Content-Type: application/json" -d \'%s\'',
    webhook_url, json_payload
)
api:executeString(cmd)

freeswitch.consoleLog("INFO", "Recording webhook posted for " .. uuid .. "\n")
