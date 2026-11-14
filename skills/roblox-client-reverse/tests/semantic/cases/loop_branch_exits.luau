local function drive(mode)
    local cursor = 1
    local log = {}

    while cursor <= 4 do
        table.insert(log, "at" .. tostring(cursor))
        if cursor == 1 then
            if mode == "first" then
                return "returned-first", table.concat(log, ",")
            end
            cursor += 1
            continue
        elseif cursor == 2 then
            if mode == "second" then
                return "returned-second", table.concat(log, ",")
            end
            cursor += 1
            continue
        else
            table.insert(log, "outer" .. tostring(cursor))
            for inner = 1, 2 do
                table.insert(log, "inner" .. tostring(cursor) .. "." .. tostring(inner))
                if mode == "break-inner" and inner == 1 then
                    break
                end
            end
            cursor += 1
        end
    end

    if false then
        table.insert(log, "unreachable-source-tail")
    end
    return "completed", table.concat(log, ",")
end

for _, mode in ipairs({"first", "second", "normal", "break-inner"}) do
    print(mode, drive(mode))
end

local function returnBeforeJoin(flag)
    if flag then
        return "early-return"
    end
    local value = "joined-after-branch"
    return value
end

print(returnBeforeJoin(true), returnBeforeJoin(false))

local function returnWithinLoop(flag)
    while true do
        if flag then
            return "loop-fast"
        end
        return "loop-fallback"
    end
    print("unreachable-source-tail")
end

print(returnWithinLoop(true), returnWithinLoop(false))
