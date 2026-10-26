--[[ appended to the DATA table by t7_zombies.py) builds the rigs like T7Zombies tools/zombie/import + publish ]]
local AS = game:GetService("AssetService")
local RS = game:GetService("ReplicatedStorage")
local STD = {
	Torso = Vector3.new(0, 3, 0), ["Left Arm"] = Vector3.new(-1.5, 3, 0), ["Right Arm"] = Vector3.new(1.5, 3, 0),
	["Left Leg"] = Vector3.new(-0.5, 1, 0), ["Right Leg"] = Vector3.new(0.5, 1, 0),
}
local dest = RS:FindFirstChild("ZombieModels") or Instance.new("Folder")
dest.Name = "ZombieModels"
dest.Parent = RS

local function build(outfit, d)
	local model = Instance.new("Model")
	model.Name = outfit
	local function piece(name, as)
		local p = d.pieces[name]
		if not p then
			return nil
		end
		local part = AS:CreateMeshPartAsync("rbxassetid://" .. p.id, { CollisionFidelity = Enum.CollisionFidelity.Box })
		part.Name = as or name
		part.Size = p.s
		part.CFrame = CFrame.new(p.c)
		part.Material = Enum.Material.SmoothPlastic
		part.Anchored, part.CanCollide, part.CanTouch, part.CastShadow = false, false, false, true
		part.CanQuery = (as == nil) and STD[name] ~= nil
		part.Massless = true
		part:SetAttribute("ZPiece", outfit .. "/" .. name)
		if p.tint then
			part:SetAttribute("Tint", p.tint)
		end
		part.Parent = model
		return part
	end
	local function box(name, size, at)
		local p = Instance.new("Part")
		p.Name = name
		p.Size = size
		p.CFrame = CFrame.new(at)
		p.Transparency = 1
		p.Anchored, p.CanCollide, p.CanQuery, p.CanTouch = false, false, false, false
		p.Parent = model
		return p
	end
	local function off(p)
		local std = STD[p.Name]
		return if std then CFrame.new(std - p.Position) else CFrame.identity
	end
	local function motor6(name, p0, p1, c0, c1)
		local m = Instance.new("Motor6D")
		m.Name, m.Part0, m.Part1, m.C0, m.C1 = name, p0, p1, off(p0) * c0, off(p1) * c1
		m.Parent = p0
	end
	local root = box("HumanoidRootPart", Vector3.new(2, 2, 1), Vector3.new(0, 3, 0))
	local torso = piece("Torso")
	local head = box("Head", Vector3.new(1.25, 1.25, 1.25), Vector3.new(0, 4.5, 0))
	head.CanQuery = true
	local la, ra = piece("Left Arm"), piece("Right Arm")
	local ll, rl = piece("Left Leg"), piece("Right Leg")
	local headMesh = piece("Head", "Head mesh")
	local R = CFrame.new(0, 0, 0, -1, 0, 0, 0, 0, 1, 0, 1, -0)
	motor6("RootJoint", root, torso, R, R)
	motor6("Neck", torso, head, CFrame.new(0, 1, 0, -1, 0, 0, 0, 0, 1, 0, 1, -0), CFrame.new(0, -0.5, 0, -1, 0, 0, 0, 0, 1, 0, 1, -0))
	motor6("Right Shoulder", torso, ra, CFrame.new(1, 0.5, 0, 0, 0, 1, 0, 1, -0, -1, -0, -0), CFrame.new(-0.5, 0.5, 0, 0, 0, 1, 0, 1, -0, -1, -0, -0))
	motor6("Left Shoulder", torso, la, CFrame.new(-1, 0.5, 0, -0, -0, -1, 0, 1, 0, 1, 0, 0), CFrame.new(0.5, 0.5, 0, -0, -0, -1, 0, 1, 0, 1, 0, 0))
	motor6("Right Hip", torso, rl, CFrame.new(1, -1, 0, 0, 0, 1, 0, 1, -0, -1, -0, -0), CFrame.new(0.5, 1, 0, 0, 0, 1, 0, 1, -0, -1, -0, -0))
	motor6("Left Hip", torso, ll, CFrame.new(-1, -1, 0, -0, -0, -1, 0, 1, 0, 1, 0, 0), CFrame.new(-0.5, 1, 0, -0, -0, -1, 0, 1, 0, 1, 0, 0))
	local function weldToHead(p)
		local w = Instance.new("Weld")
		w.Name = p.Name
		w.Part0, w.Part1 = head, p
		w.C0 = head.CFrame:Inverse() * p.CFrame
		w.Parent = head
	end
	weldToHead(headMesh)
	for name in d.pieces do
		if not STD[name] and name ~= "Head" then
			local p = piece(name)
			if p then
				p:SetAttribute("Accessory", true)
				weldToHead(p)
			end
		end
	end
	local sf = Instance.new("Folder")
	sf.Name = "Skins"
	sf.Parent = model
	for skin, id in d.skins do
		local sa = Instance.new("SurfaceAppearance")
		sa.Name = skin
		sa.AlphaMode = Enum.AlphaMode.Overlay
		if not pcall(function() sa.ColorMapContent = Content.fromAssetId(id) end) then
			sa.ColorMap = "rbxassetid://" .. id
		end
		sa.Parent = sf
	end
	model:SetAttribute("EyeL", head.CFrame:PointToObjectSpace(d.eyeL))
	model:SetAttribute("EyeR", head.CFrame:PointToObjectSpace(d.eyeR))
	model.PrimaryPart = root
	model.WorldPivot = CFrame.new(0, 3, 0)
	local old = dest:FindFirstChild(outfit)
	if old then
		old:Destroy()
	end
	model:PivotTo(CFrame.new(0, 3, 0))
	model.Parent = dest
end

local made = {}
for outfit, d in DATA do
	local ok, err = pcall(build, outfit, d)
	table.insert(made, outfit .. "=" .. (if ok then "ok" else tostring(err)))
end
print("[ZRIG] " .. table.concat(made, ", "))
