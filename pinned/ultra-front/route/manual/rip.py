import pcbnew, sys, json
b=pcbnew.LoadBoard(sys.argv[1]); nets=sys.argv[2].split(',')
ops=[{"op":"remove","uuid":t.m_Uuid.AsString()} for t in b.GetTracks() if t.GetNetname() in nets]
json.dump({"version":1,"ops":ops},open(sys.argv[3],'w')); print(len(ops))
