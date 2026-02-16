import os
p='logs/predator.log'
if not os.path.exists(p):
    print('Log not found:', p)
else:
    with open(p,'r',encoding='utf-8',errors='ignore') as f:
        lines=f.readlines()[-200:]
        print('--- last log lines ---')
        for l in lines:
            print(l.rstrip())
