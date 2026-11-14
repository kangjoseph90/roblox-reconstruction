type Entry = { name: string, score: number }

local function makeCounter(start: number): () -> number
	local value = start
	return function(): number
		value += 1
		return value
	end
end

local entries: {Entry} = {
	{ name = "Ada", score = 4 },
	{ name = "Lin", score = 7 },
}
local nextValue = makeCounter(2)
local total = 0
for _, entry in ipairs(entries) do
	for i = 1, entry.score do
		if i % 2 == 0 then
			total += i
		end
	end
end

local result = { total = total, count = nextValue(), label = entries[1].name }
print(result.total, result.count, result.label)
