local log = {}
local function effect(x) log[#log + 1] = x; return x end
local function fail(kind)
    log[#log + 1] = "raise:" .. kind
    error("authored:" .. kind)
end
local a = effect("first") and effect("second") or effect("fallback")
local ok1, err1 = pcall(function() effect("inside"); fail("zero") end)
local ok2, result = pcall(function() return effect("safe") end)
local category = tostring(err1):match("authored:([%w_%-]+)") or "missing"
print("OBS|result=" .. tostring(a) .. "," .. tostring(ok1) .. "," .. category .. "," .. tostring(ok2) .. "," .. tostring(result))
print("OBS|order=" .. table.concat(log, ","))
