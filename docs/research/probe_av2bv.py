
XOR=23442827791579; MASK=2251799813685247; BASE=58
D="FcwAPNKTMug3GV5Lj7EJnHpWsx4tb8haYeviqBz6rkCy12mUSDQX9RdoZf"
def av2bv(aid):
    b=list("BV1000000000")
    tmp=(1<<51 | aid) ^ XOR
    i=len(b)-1
    while tmp>0:
        b[i]=D[tmp%BASE]; tmp//=BASE; i-=1
    b[3],b[9]=b[9],b[3]; b[4],b[7]=b[7],b[4]
    return "".join(b)
for aid,exp in [(170001,"BV17x411w7KC"),(2,"BV1xx411c7mD"),(117562180376697,"BV1bw4t6fET9"),(99999999,None)]:
    got=av2bv(aid)
    print(aid, got, exp, got==exp)
