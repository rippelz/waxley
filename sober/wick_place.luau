--[[
wick_place.luau (lune): first half of sober/wick_place.py. Rewrites a place so the Android engine can run
its client code when Studio hosts it.

Studio's server replicates scripts as Source, and the engine (no compiler) only runs bytecode, so:
  - LocalScripts and Client-RunContext Scripts: Source becomes a placeholder, later replaced with wickc's
    bytecode (the server never runs them)
  - ModuleScripts outside ServerScriptService/ServerStorage: same, and the original source moves to a twin in
    ServerStorage.__WickSource (attribute WickSource on the original names it); every server-side chunk
    starts with a one-line `require` shim that sends a require of such a module to its twin, and a twin's
    `script` is the original, so script.Parent paths still work. Line numbers don't change.
  - PlayerModule, PlayerScriptsLoader, RbxCharacterSounds and Animate (which Studio would insert as source)
    are added from Studio's own copies when the place has none

  lune run wick_place.luau <in.rbxl|rbxlx> <out.rbxl> <work dir> <studio dir>
Writes <work>/sources/<n>.luau and <work>/manifest.json ({placeholder: source file}).
]]
local fs = require("@lune/fs")
local process = require("@lune/process")
local roblox = require("@lune/roblox")
local serde = require("@lune/serde")

local inPath, outPath, work, studio = table.unpack(process.args)
assert(inPath and outPath and work and studio, "usage: wick_place.luau <in> <out.rbxl> <work dir> <studio dir>")

local game = roblox.deserializePlace(fs.readFile(inPath))
local Enum = roblox.Enum

-- ---------- Studio's default player/character scripts ----------
local PS = studio .. "/ExtraContent/scripts/PlayerScripts"

local function scriptFromFile(class, name, path)
	local s = roblox.Instance.new(class)
	s.Name = name
	s.Source = fs.readFile(path)
	return s
end

-- Rojo-style tree: X.lua (+ X/ for its children) is a ModuleScript, a bare directory is a Folder
local function moduleTree(name, file, dir)
	local inst = if file then scriptFromFile("ModuleScript", name, file) else roblox.Instance.new("Folder")
	if not file then
		inst.Name = name
	end
	if dir and fs.isDir(dir) then
		local entries = fs.readDir(dir)
		table.sort(entries)
		local seen = {}
		for _, e in entries do
			local child = e:match("^(.-)%.lua$") or e
			if not seen[child] then
				seen[child] = true
				local f = dir .. "/" .. child .. ".lua"
				local d = dir .. "/" .. child
				moduleTree(child, if fs.isFile(f) then f else nil, if fs.isDir(d) then d else nil).Parent = inst
			end
		end
	end
	return inst
end

local starterPlayer = game:GetService("StarterPlayer")
local sps = starterPlayer:FindFirstChildOfClass("StarterPlayerScripts")
if not sps then
	sps = roblox.Instance.new("StarterPlayerScripts")
	sps.Parent = starterPlayer
end
local scs = starterPlayer:FindFirstChildOfClass("StarterCharacterScripts")
if not scs then
	scs = roblox.Instance.new("StarterCharacterScripts")
	scs.Parent = starterPlayer
end
if not sps:FindFirstChild("PlayerModule") then
	moduleTree("PlayerModule", PS .. "/StarterPlayerScripts/PlayerModule.module.lua", PS .. "/StarterPlayerScripts/PlayerModule.module").Parent =
		sps
end
if not sps:FindFirstChild("PlayerScriptsLoader") then
	scriptFromFile("LocalScript", "PlayerScriptsLoader", PS .. "/StarterPlayerScripts/PlayerScriptsLoader.lua").Parent = sps
end
if not sps:FindFirstChild("RbxCharacterSounds") then
	local s = scriptFromFile("LocalScript", "RbxCharacterSounds", PS .. "/StarterPlayerScriptsCommon/RbxCharacterSounds.lua")
	scriptFromFile("ModuleScript", "AtomicBinding", PS .. "/StarterPlayerScriptsCommon/RbxCharacterSounds/AtomicBinding.lua").Parent = s
	s.Parent = sps
end
if not scs:FindFirstChild("Animate") then
	local s = scriptFromFile("LocalScript", "Animate", studio .. "/content/avatar/scripts/humanoidAnimateR15.lua")
	local emote = roblox.Instance.new("BindableFunction")
	emote.Name = "PlayEmote"
	emote.Parent = s
	s.Parent = scs
end

-- ---------- client scripts -> bytecode placeholders, shared modules -> twins ----------
local serverStorage = game:GetService("ServerStorage")
local serverScriptService = game:GetService("ServerScriptService")
local twins = roblox.Instance.new("Folder")
twins.Name = "__WickSource"

local SHIM = [[
-- wick: requires of a module whose Source is client bytecode go to its server twin (attribute WickSource)
local folder = script.Parent
return function(req)
	return function(m)
		if typeof(m) == "Instance" and m:IsA("ModuleScript") then
			local id = m:GetAttribute("WickSource")
			local twin = id and folder:FindFirstChild(id)
			if twin then
				return req(twin)
			end
		end
		return req(m)
	end
end
]]
local PRELUDE = 'local require = require(game:GetService("ServerStorage"):WaitForChild("__WickSource"):WaitForChild("Require"))(require); '

local function serverSide(d)
	return d:IsDescendantOf(serverStorage) or d:IsDescendantOf(serverScriptService)
end

local manifest = {}
local n = 0
local function toBytecode(d)
	n += 1
	local key = string.format("\1WICKBC:%d\1", n)
	local file = string.format("%s/sources/%d.luau", work, n)
	fs.writeFile(file, d.Source)
	manifest[key] = file
	d.Source = key
end

fs.writeDir(work .. "/sources")
local counts = { localscripts = 0, modules = 0, twins = 0, server = 0 }
local serverChunks = {}
for _, d in game:GetDescendants() do
	local c = d.ClassName
	if c == "LocalScript" or (c == "Script" and d.RunContext == Enum.RunContext.Client) then
		toBytecode(d)
		counts.localscripts += 1
	elseif c == "ModuleScript" then
		if serverSide(d) then
			table.insert(serverChunks, d)
		else
			local twin = roblox.Instance.new("ModuleScript")
			counts.twins += 1
			twin.Name = "M" .. counts.twins
			local orig = roblox.Instance.new("ObjectValue")
			orig.Name = "Original"
			orig.Value = d
			orig.Parent = twin
			-- `script` is the original (for script.Parent paths); one line, so line numbers stay put
			twin.Source = "local script = script.Original.Value; " .. PRELUDE .. d.Source
			twin.Parent = twins
			d:SetAttribute("WickSource", twin.Name)
			toBytecode(d)
			counts.modules += 1
		end
	elseif c == "Script" then
		table.insert(serverChunks, d)
	end
end
for _, d in serverChunks do
	d.Source = PRELUDE .. d.Source
	counts.server += 1
end
local shim = roblox.Instance.new("ModuleScript")
shim.Name = "Require"
shim.Source = SHIM
shim.Parent = twins
twins.Parent = serverStorage

fs.writeFile(work .. "/manifest.json", serde.encode("json", manifest))
fs.writeFile(outPath, roblox.serializePlace(game))
print(serde.encode("json", counts))
