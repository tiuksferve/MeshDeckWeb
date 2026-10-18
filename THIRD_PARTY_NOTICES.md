# Third-Party Notices

MeshDeckWeb is released under the [MIT License](LICENSE). It includes, or
depends on, the third-party software listed below, each under its own
license.

---

## Included in this repository

These libraries are vendored in `web/vendor/` and served to the browser as
they are.

### Chart.js 4.4.0 — `web/vendor/chart.umd.min.js`

https://www.chartjs.org

```
The MIT License (MIT)

Copyright (c) 2014-2022 Chart.js Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### Leaflet 1.9.4 — `web/vendor/leaflet/`

https://leafletjs.com

```
BSD 2-Clause License

Copyright (c) 2010-2023, Volodymyr Agafonkin
Copyright (c) 2010-2011, CloudMade
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

### Socket.IO client 4.7.5 — `web/vendor/socket.io.min.js`

https://socket.io

```
(The MIT License)

Copyright (c) 2014-2024 Guillermo Rauch

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## Installed separately

These Python packages are not part of this repository. `pip` installs them
from `requirements.txt`, and each keeps its own license.

| Package | License |
|---------|---------|
| [meshtastic](https://github.com/meshtastic/python) | GPL-3.0-only |
| [Flask](https://flask.palletsprojects.com) | BSD-3-Clause |
| [Flask-SocketIO](https://github.com/miguelgrinberg/Flask-SocketIO) | MIT |
| [simple-websocket](https://github.com/miguelgrinberg/simple-websocket) | MIT |
| [pypubsub](https://github.com/schollii/pypubsub) | BSD-2-Clause |
| [pyserial](https://github.com/pyserial/pyserial) | BSD-3-Clause |
| [cryptography](https://github.com/pyca/cryptography) | Apache-2.0 OR BSD-3-Clause |
| [certifi](https://github.com/certifi/python-certifi) | MPL-2.0 |

The `meshtastic` package is licensed under the GPL-3.0. MeshDeckWeb only
imports it and does not include its code, so MeshDeckWeb's own source stays
under the MIT License. Anyone who distributes MeshDeckWeb bundled together
with `meshtastic` (for example, as a single package or image) must also
meet the terms of the GPL-3.0 for that bundle.

The offline map shows tiles from [OpenStreetMap](https://www.openstreetmap.org/copyright),
© OpenStreetMap contributors, under the Open Database License (ODbL).

---

## Trademarks

Meshtastic® is a registered trademark of Meshtastic LLC. MeshDeckWeb is an
independent project, not affiliated with or endorsed by Meshtastic LLC; the
name is used only to say which devices it works with.
