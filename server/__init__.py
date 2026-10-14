"""
server — Servidor web do MeshDeck: Flask + Socket.IO sobre core/.

A ligação ao nó Meshtastic vive no processo do servidor; o browser é só
interface. Um navegador não consegue abrir um socket TCP para a porta 4403
nem reimplementar a pilha protobuf do Meshtastic, por isso é o servidor que
mantém a sessão e difunde o que acontece.
"""
