local trace = {}
local function mark(x) trace[#trace + 1] = x; return x end
local co = coroutine.create(function(start)
    local got = mark(start + 1)
    local incoming = coroutine.yield("pause", got)
    return mark(incoming * 2), mark("done")
end)
local ok1, a, b = coroutine.resume(co, 4)
local callback = function(x, y) mark("cb:" .. tostring(x) .. ":" .. tostring(y)); return #tostring(x) + y end
local resumeValue = callback(a, b)
local ok2, c, d = coroutine.resume(co, resumeValue)
print("OBS|co=" .. tostring(ok1) .. "," .. tostring(ok2) .. "," .. tostring(a) .. "," .. tostring(b) .. "," .. tostring(c) .. "," .. tostring(d))
print("OBS|trace=" .. table.concat(trace, ","))
