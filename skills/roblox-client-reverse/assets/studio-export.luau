-- Execute only in Studio on an owned or explicitly permitted place.
-- This is a command-bar snippet. It returns a self-contained rcr-dump/v1 JSON string.
-- For large source exports prefer File > Save to File (.rbxl/.rbxlx).
-- Set mode to runtime-metadata when observing the client view during Play.
local mode = "edit-source" -- edit-source | runtime-metadata
local RunService = game:GetService("RunService")
assert(RunService:IsStudio(), "Use an authorized Studio session")
assert(mode == "edit-source" or mode == "runtime-metadata", "Unknown export mode")
if mode == "edit-source" then
    assert(not RunService:IsRunning(), "Source export requires Studio edit mode")
else
    assert(RunService:IsRunning() and RunService:IsClient(), "Use the Studio client during Play")
end
local all = game:GetDescendants()
assert(#all <= 200000, "Export instance limit exceeded")
local ids = {}
for i, instance in all do ids[instance] = tostring(i) end
local records = {}
local ScriptEditorService = game:GetService("ScriptEditorService")
for _, instance in all do
    local record = {
        id = ids[instance],
        class = instance.ClassName,
        name = instance.Name,
        parent = ids[instance.Parent],
        metadata = {},
    }
    if instance:IsA("LuaSourceContainer") then
        if mode == "edit-source" then
            local ok, value = pcall(function()
                return ScriptEditorService:GetEditorSource(instance)
            end)
            if ok then record.source = value
            else record.metadata.source_error = tostring(value) end
        else
            record.metadata.source_error = "Runtime metadata capture has no source/bytecode API"
        end
    end
    if instance:IsA("BaseScript") then
        record.metadata.Disabled = instance.Disabled
    end
    if instance:IsA("Script") then
        record.metadata.RunContext = instance.RunContext.Value
    end
    -- Attribute values can include datatypes JSON cannot encode; preserve typed strings.
    local attrs = {}
    for key, value in instance:GetAttributes() do
        attrs[key] = {type = typeof(value), value = tostring(value)}
    end
    record.metadata.attributes = attrs
    record.metadata.tags = instance:GetTags()
    table.insert(records, record)
end
local capture = {
    mode = mode,
    place_id = tostring(game.PlaceId),
    place_version = game.PlaceVersion,
    job_id = game.JobId,
    timestamp = DateTime.now():ToIsoDate(),
    streaming_enabled = workspace.StreamingEnabled,
    completeness = "single snapshot; excludes unreplicated, unparented and unavailable objects",
    source_access = mode == "edit-source" and "Studio editor source via protected API" or "none",
}
return game:GetService("HttpService"):JSONEncode({schema = "rcr-dump/v1", capture = capture, instances = records})
