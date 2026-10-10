import re
import json

with open('/tmp/ks.html', 'r') as f:
    html = f.read()

match = re.search(r'window\.__APOLLO_STATE__=({.*?});', html)
if match:
    state_str = match.group(1)
    with open('/tmp/ks_state.json', 'w') as f:
        # Just write it as a string first to see it since JS syntax might break simple JSON decoding if undefined/Map are there.
        f.write(state_str)
    print("Apollo state extracted to /tmp/ks_state.json")
else:
    print("Apollo state not found")
