import json
open('/home/ubuntu/v3-bot/chop_state.json', 'w').write(json.dumps({'history': []}))
print('chop reset')
