"""Regenerates items.json and merge-vectors.json (run from the repo root: python3 packages/shared/fixtures/ink/generate.py).

Hand-written cases; the expected merge results are checked by both test suites, not computed here."""
import json
ID="019a8c4e-0000-7000-8000-00000000000"
def sid(n): return f"019a8c4e-0000-7000-8000-{n:012x}"
T=1_763_000_000_000
pen={"id":sid(1),"tool":"pen","color":"ink","size":0.002,"points":[[0.1,0.1,0.5,0],[0.2,0.15,0.6,8]],"createdAt":T,"updatedAt":T}
text={"id":sid(2),"tool":"text","color":"violet","size":0.02,"x":0.1,"y":0.3,"w":0.4,"text":"Type 4 = aldo ↓","createdAt":T,"updatedAt":T}
def w(base,**kw):
    d=dict(base); 
    for k,v in kw.items():
        if v is ...: d.pop(k,None)
        else: d[k]=v
    return d
cases=[
 ("pen stroke",True,pen),
 ("marker with shape",True,w(pen,tool="marker",color="lime",size=0.02,shape="line")),
 ("text box with anchor",True,w(text,anchor={"pm":42,"dy":0.01})),
 ("anchor with dx and dy",True,w(pen,anchor={"pm":0,"dx":-0.5,"dy":1.25})),
 ("tombstone",True,w(pen,deleted=True)),
 ("integer-valued float timestamp",True,w(pen,createdAt=float(T))),
 ("unrounded su precision is accepted",True,w(pen,points=[[0.1234567891,0.98765432,0.333333333,0]])),
 ("negative su inside the bound",True,w(pen,points=[[-10000,10000,0,86400000]])),
 ("one point",True,w(pen,points=[[0.5,0.5,1,0]])),
 ("max size pen",True,w(pen,size=0.1)),
 ("text of 2000 ascii chars",True,w(text,text="a"*2000)),
 ("text of 2000 emoji (zod counts code points)",True,w(text,text="😀"*2000)),
 ("max points",True,w(pen,points="$points:10000")),
 ("text box w at max",True,w(text,w=4)),
 # invalid
 ("no points",False,w(pen,points=[])),
 ("too many points",False,w(pen,points="$points:10001")),
 ("pressure above 1",False,w(pen,points=[[0,0,1.5,0]])),
 ("fractional point time",False,w(pen,points=[[0,0,0.5,1.5]])),
 ("point time over a day",False,w(pen,points=[[0,0,0.5,86400001]])),
 ("point with five values",False,w(pen,points=[[0,0,0.5,0,1]])),
 ("point with three values",False,w(pen,points=[[0,0,0.5]])),
 ("su out of bound",False,w(pen,points=[[10000.5,0,0.5,0]])),
 ("string coordinate",False,w(pen,points=[["0.1",0,0.5,0]])),
 ("boolean pressure",False,w(pen,points=[[0,0,True,0]])),
 ("hex colour",False,w(pen,color="#ff0000")),
 ("extra key",False,w(pen,extra=1)),
 ("not a uuid",False,w(pen,id="not-a-uuid")),
 ("uuid v4",False,w(pen,id="0190a0b2-7c3d-4e5f-8a1b-2c3d4e5f6a7b")),
 ("uppercase uuid v7",False,w(pen,id=sid(1).upper())),
 ("null anchor",False,w(pen,anchor=None)),
 ("null shape",False,w(pen,shape=None)),
 ("deleted false",False,w(pen,deleted=False)),
 ("deleted null",False,w(pen,deleted=None)),
 ("string updatedAt",False,w(pen,updatedAt=str(T))),
 ("fractional updatedAt",False,w(pen,updatedAt=T+0.5)),
 ("negative createdAt",False,w(pen,createdAt=-1)),
 ("unsafe integer",False,w(pen,updatedAt=2**53)),
 ("boolean size",False,w(pen,size=True)),
 ("zero size",False,w(pen,size=0)),
 ("pen size over max",False,w(pen,size=0.11)),
 ("unknown tool",False,w(pen,tool="brush")),
 ("text tool on a stroke",False,w(pen,tool="text")),
 ("stroke with text fields",False,w(text,tool="pen")),
 ("empty text",False,w(text,text="")),
 ("text of 2001 chars",False,w(text,text="a"*2001)),
 ("text of 2001 emoji",False,w(text,text="😀"*2001)),
 ("text box w over max",False,w(text,w=4.01)),
 ("text box missing x",False,w(text,x=...)),
 ("anchor fractional pm",False,w(pen,anchor={"pm":1.5})),
 ("anchor negative pm",False,w(pen,anchor={"pm":-1})),
 ("anchor extra key",False,w(pen,anchor={"pm":1,"dz":0})),
 ("anchor without pm",False,w(pen,anchor={"dy":0.1})),
 ("unknown shape",False,w(pen,shape="star")),
 ("missing color",False,w(pen,color=...)),
 ("array instead of object",False,[1,2]),
 ("null item",False,None),
]
json.dump({"comment":"A points value \"$points:N\" expands to N points [0.1, 0.1, 0.5, i] before parsing. InkItem parity fixtures: InkItemSchema (zod, packages/shared/src/ink.fixtures.test.ts) and the pydantic mirror (apps/api/tests/ink/test_models.py) must agree on every case.","cases":[{"name":n,"valid":v,"item":i} for n,v,i in cases]},open("packages/shared/fixtures/ink/items.json","w"),ensure_ascii=False,separators=(",",":"))

# merge golden vectors: replicas a and b; expected = merge(a,b)=merge(b,a) (server: apply a then b, and b then a)
def stroke(n,u,**kw): return w(pen,id=sid(n),createdAt=T,updatedAt=u,**kw)
vec=[]
def case(name,a,b,expected,note=""):
    vec.append({"name":name,"a":a,"b":b,"expected":sorted(expected,key=lambda i:i["id"]),"note":note})
x1=stroke(10,T+10); x1m=stroke(10,T+20,points=[[0.3,0.3,0.5,0]])
case("newer edit wins",[x1],[x1m],[x1m])
case("disjoint items are unioned",[stroke(11,T)],[stroke(12,T)],[stroke(11,T),stroke(12,T)])
dead=stroke(13,T+30,deleted=True); edit=stroke(13,T+20,color="violet")
case("newer tombstone beats an older edit",[edit],[dead],[dead])
case("newer edit beats an older tombstone (undo restamp)",[stroke(14,T+40,color="violet")],[stroke(14,T+30,deleted=True)],[stroke(14,T+40,color="violet")])
case("tie: tombstone beats a concurrent edit",[stroke(15,T+30,color="violet")],[stroke(15,T+30,deleted=True)],[stroke(15,T+30,deleted=True)])
case("tie: identical copies",[stroke(16,T+5)],[stroke(16,T+5)],[stroke(16,T+5)])
case("identical tombstones",[stroke(17,T+5,deleted=True)],[stroke(17,T+5,deleted=True)],[stroke(17,T+5,deleted=True)])
case("old copy never resurrects a tombstone",[stroke(18,T,)],[stroke(18,T+1,deleted=True)],[stroke(18,T+1,deleted=True)])
skA=stroke(19,T+240_000,color="violet"); skB=stroke(19,T+60_000,color="lime")
case("clock skew within 4 minutes: the device ahead wins on both sides",[skA],[skB],[skA])
case("text box edit vs stroke tombstone on different ids",[w(text,id=sid(20),updatedAt=T+3)],[stroke(21,T+4,deleted=True)],[w(text,id=sid(20),updatedAt=T+3),stroke(21,T+4,deleted=True)])
multiA=[stroke(22,T+1),stroke(23,T+9,color="violet"),stroke(24,T+5,deleted=True)]
multiB=[stroke(22,T+2,color="lime"),stroke(23,T+8),stroke(24,T+5,color="amber"),stroke(25,T)]
case("several items at once",multiA,multiB,[stroke(22,T+2,color="lime"),stroke(23,T+9,color="violet"),stroke(24,T+5,deleted=True),stroke(25,T)])
json.dump({"comment":"Concurrent-edit golden vectors: mergeInkItems(a,b) == mergeInkItems(b,a) == expected (packages/shared/src/ink.fixtures.test.ts), and the API's SQL upsert gives expected whichever replica is written first (apps/api/tests/ink/test_ink_db.py). Full ties with different content are excluded on purpose: the server keeps its stored row and clients adopt it.","base_ms":T,"cases":vec},open("packages/shared/fixtures/ink/merge-vectors.json","w"),ensure_ascii=False,indent=1)
