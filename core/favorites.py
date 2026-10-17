"""
core/favorites.py — Favoritos geridos no firmware do nó local.

"""

from typing import Dict, Optional, Set

from constants import logger


class FirmwareFavorites:
    """
    Gestão de favoritos directamente no firmware do nó local.
    Lê isFavorite do nodesByNum (preenchido pelo daemon ao conectar).
    Escreve via localNode.setFavorite() / removeFavorite() (AdminMessage).
    Não usa ficheiro local — a fonte de verdade é sempre o firmware.
    """

    def __init__(self):
        self._iface = None                  # definido em set_interface() após ligação
        self._favorites: Set[str] = set()   # cache em memória (id_string)

    def set_interface(self, iface):
        """Chamado após ligação — carrega favoritos do nodesByNum do firmware."""
        self._iface = iface
        self._favorites = set()
        if iface is None:
            return
        try:
            nodes = getattr(iface, 'nodesByNum', {}) or {}
            for num, node in nodes.items():
                if node.get('isFavorite'):
                    uid = node.get('user', {}).get('id', '')
                    if uid:
                        self._favorites.add(uid)
        except Exception as e:
            logger.warning(f"FirmwareFavorites.load: {e}")

    def is_favorite(self, node_id: str) -> bool:
        return node_id in self._favorites

    def toggle(self, node_id: str, node_data: Optional[Dict] = None) -> bool:
        """Alterna favorito no firmware. Retorna True se agora é favorito."""
        if node_id in self._favorites:
            self._favorites.discard(node_id)
            self._send_to_firmware(node_id, favorite=False)
            return False
        else:
            self._favorites.add(node_id)
            self._send_to_firmware(node_id, favorite=True)
            return True

    def _send_to_firmware(self, node_id: str, favorite: bool):
        """Envia setFavorite ou removeFavorite ao nó local via AdminMessage."""
        if not self._iface:
            logger.warning("FirmwareFavorites: no interface — could not persist")
            return
        try:
            local = self._iface.localNode
            if favorite:
                local.setFavorite(node_id)
                logger.info(f"Favourite added in firmware: {node_id}")
            else:
                local.removeFavorite(node_id)
                logger.info(f"Favourite removed from firmware: {node_id}")
        except Exception as e:
            logger.warning(f"FirmwareFavorites._send_to_firmware: {e}")

    def get_all(self) -> Set[str]:
        return set(self._favorites)
