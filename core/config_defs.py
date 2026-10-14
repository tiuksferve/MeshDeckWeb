"""
core/config_defs.py — Esquema das 21 secções de configuração do firmware.

É a fonte de verdade sobre
que campos existem, de que tipo são e que limites têm, validada contra o
config.proto e module_config.proto oficiais.

O servidor serve-o como JSON e um renderizador genérico no browser gera os
formulários das 21 secções a partir dele.

Formato de cada campo: (etiqueta, nome_no_protobuf, tipo, opções)
  "combo"              -> opções é a lista de valores do enum
  "bool"               -> opções é None
  "spin_int"           -> opções é (mínimo, máximo)
  "spin_float"         -> opções é (mínimo, máximo)
  "text"               -> opções é None
  "readonly"           -> mostrado mas não editável
  "readonly_with_note" -> idem, com uma nota explicativa nas opções
  "canned_messages"    -> editor dedicado das mensagens pré-definidas
O nome do campo pode conter pontos para campos aninhados (ex: "ipv4_config.ip").
"""

import re

from constants import logger
from i18n import tr


MESHTASTIC_CONFIG_DEFS = {
    "localConfig.device": [
        ("Papel do nó",             "role",                    "combo",
         ["CLIENT","CLIENT_MUTE","CLIENT_HIDDEN","CLIENT_BASE","TRACKER","LOST_AND_FOUND",
          "SENSOR","TAK","TAK_TRACKER","REPEATER","ROUTER","ROUTER_CLIENT","ROUTER_LATE"]),
        ("Retransmitir mensagens",  "rebroadcast_mode",        "combo",
         ["ALL","ALL_SKIP_DECODING","LOCAL_ONLY","KNOWN_ONLY","NONE","CORE_PORTNUMS_ONLY"]),
        # serial_enabled passou para Segurança (obsoleto aqui); debug_log_enabled
        # e quick_chat_button já não existem no proto DeviceConfig.
        ("Botão GPIO",              "button_gpio",             "spin_int",(0,39)),
        ("Buzzer GPIO",             "buzzer_gpio",             "spin_int",(0,39)),
        ("Duplo clique alimentação","double_tap_as_button_press","bool",  None),
        ("LED em heartbeat",        "led_heartbeat_disabled",  "bool",   None),
        ("Intervalo broadcast NodeInfo (s)", "node_info_broadcast_secs","spin_int",(0,604800)),
        ("Fuso horário (TZ string)","tzdef",                   "text",   None),
        ("Disable triple-click",    "disable_triple_click",    "bool",   None),
        ("Modo do buzzer",          "buzzer_mode",             "combo",
         ["ALL_ENABLED","DISABLED","NOTIFICATIONS_ONLY","SYSTEM_ONLY","DIRECT_MSG_ONLY"]),
    ],
    "localConfig.position": [
        ("Modo GPS",                "gps_mode",                "combo",
         ["DISABLED","ENABLED","NOT_PRESENT"]),
        ("Intervalo update GPS (s)","gps_update_interval",     "spin_int",(0,86400)),
        ("Tentativa GPS (s)",       "gps_attempt_time",        "spin_int",(0,3600)),
        ("Intervalo broadcast pos (s)","position_broadcast_secs","spin_int",(0,86400)),
        ("Smart broadcast pos.",    "position_broadcast_smart_enabled","bool",None),
        ("Distância mínima smart (m)","broadcast_smart_minimum_distance","spin_int",(0,10000)),
        ("Intervalo mínimo smart (s)","broadcast_smart_minimum_interval_secs","spin_int",(0,3600)),
        # A posição fixa liga-se aqui; as coordenadas definem-se por mensagem
        # admin própria (set_fixed_position), não fazem parte do PositionConfig.
        ("Posição fixa",            "fixed_position",          "bool",   None),
        ("Precision de posição",    "position_flags",          "spin_int",(0,8191)),
        ("Receiver GPIO",           "rx_gpio",                 "spin_int",(0,39)),
        ("Transmitter GPIO",        "tx_gpio",                 "spin_int",(0,39)),
        ("GPIO de activação do GPS","gps_en_gpio",             "spin_int",(0,48)),
        # gps_accept_2d e gps_max_dop já não existem no proto PositionConfig.
    ],
    "localConfig.power": [
        ("Modo de economia",        "is_power_saving",         "bool",   None),
        ("Desligar na bateria (s)", "on_battery_shutdown_after_secs","spin_int",(0,86400)),
        ("Override ADC multiplicador","adc_multiplier_override","spin_float",(0.0,10.0)),
        ("Wait Bluetooth (s)",      "wait_bluetooth_secs",     "spin_int",(0,3600)),
        ("Modo SDS desligar (s)",   "sds_secs",                "spin_int",(0,86400)),
        ("Modo LS desligar (s)",    "ls_secs",                 "spin_int",(0,86400)),
        ("Tempo mínimo acordado (s)","min_wake_secs",          "spin_int",(0,3600)),
        ("INA endereço I2C bateria","device_battery_ina_address","spin_int",(0,127)),
        ("Powersave GPIO",          "powermon_enables",        "spin_int",(0,65535)),
    ],
    "localConfig.network": [
        ("WiFi habilitado",         "wifi_enabled",            "bool",   None),
        ("SSID WiFi",               "wifi_ssid",               "text",   None),
        ("Senha WiFi",              "wifi_psk",                "text",   None),
        ("Servidor NTP",            "ntp_server",              "text",   None),
        ("Ethernet habilitada",     "eth_enabled",             "bool",   None),
        ("Modo endereçamento",      "address_mode",            "combo",  ["DHCP","STATIC"]),
        ("IP estático",             "ipv4_config.ip",          "text",   None),
        ("Gateway",                 "ipv4_config.gateway",     "text",   None),
        ("Subnet",                  "ipv4_config.subnet",      "text",   None),
        ("DNS",                     "ipv4_config.dns",         "text",   None),
        ("Servidor rsyslog",        "rsyslog_server",          "text",   None),
        ("IPv6 habilitado",         "ipv6_enabled",            "bool",   None),
        ("Protocolos (1 = UDP broadcast)","enabled_protocols", "spin_int",(0,1)),
    ],
    "localConfig.display": [
        ("Ecrã ligado (s)",         "screen_on_secs",          "spin_int",(0,3600)),
        ("Múltiplo do auto dim.",   "auto_screen_carousel_secs","spin_int",(0,3600)),
        ("Unidades",                "units",                   "combo",  ["METRIC","IMPERIAL"]),
        ("OLED tipo",               "oled",                    "combo",
         ["OLED_AUTO","OLED_SSD1306","OLED_SH1106","OLED_SH1107",
          "OLED_SH1107_128_128","OLED_SH1107_ROTATED"]),
        ("Modo do display",         "displaymode",             "combo",
         ["DEFAULT","TWOCOLOR","INVERTED","COLOR"]),
        ("Flip ecrã",               "flip_screen",             "bool",   None),
        ("Acord. por toque/mov.",   "wake_on_tap_or_motion",   "bool",   None),
        ("Cabeçalho negrito",       "heading_bold",            "bool",   None),
        ("Orientação da bússola",   "compass_orientation",     "combo",
         ["DEGREES_0","DEGREES_90","DEGREES_180","DEGREES_270",
          "DEGREES_0_INVERTED","DEGREES_90_INVERTED","DEGREES_180_INVERTED","DEGREES_270_INVERTED"]),
        ("Relógio de 12 horas",     "use_12h_clock",           "bool",   None),
        ("Nome longo do nó no ecrã","use_long_node_name",      "bool",   None),
        ("Mensagens em balões",     "enable_message_bubbles",  "bool",   None),
        # gps_format e compass_north_top são obsoletos no proto DisplayConfig.
    ],
    "localConfig.lora": [
        ("Usar preset",             "use_preset",              "bool",   None),
        ("Modem preset",            "modem_preset",            "combo",
         ["LONG_FAST","LONG_SLOW","VERY_LONG_SLOW","MEDIUM_SLOW",
          "MEDIUM_FAST","SHORT_SLOW","SHORT_FAST","LONG_MODERATE",
          "SHORT_TURBO","LONG_TURBO","LITE_FAST","LITE_SLOW","NARROW_FAST","NARROW_SLOW"]),
        ("Região",                  "region",                  "combo",
         ["UNSET","US","EU_433","EU_868","CN","JP","ANZ","KR","TW",
          "RU","IN","NZ_865","TH","LORA_24","UA_433","UA_868",
          "MY_433","MY_919","SG_923","PH_433","PH_868","PH_915",
          "ANZ_433","KZ_433","KZ_863","NP_865","BR_902",
          "EU_866","EU_874","EU_917","EU_N_868","ITU1_2M","ITU2_2M","ITU3_2M"]),
        ("Largura de banda",        "bandwidth",               "spin_int",(0,500)),
        ("Spreading factor",        "spread_factor",           "spin_int",(7,12)),
        ("Coding rate",             "coding_rate",             "spin_int",(5,8)),
        ("Offset frequência (MHz)", "frequency_offset",        "spin_float",(-100.0,100.0)),
        ("TX habilitado",           "tx_enabled",              "bool",   None),
        ("TX Power (dBm)",          "tx_power",                "spin_int",(0,30)),
        ("Hop limit",               "hop_limit",               "spin_int",(1,7)),
        ("Ignorar MQTT",            "ignore_mqtt",             "bool",   None),
        ("Override duty cycle",     "override_duty_cycle",     "bool",   None),
        ("Override frequency (MHz)","override_frequency",      "spin_float",(0.0,1000.0)),
        ("RX boosted gain (SX126x)","sx126x_rx_boosted_gain",  "bool",   None),
        ("PA fan GPIO",             "pa_fan_disabled",         "bool",   None),
        ("OK para MQTT",            "config_ok_to_mqtt",       "bool",   None),
        ("Número do canal (0-7)",   "channel_num",             "spin_int",(0,7)),
        ("Modo LNA do FEM",         "fem_lna_mode",            "combo",
         ["DISABLED","ENABLED","NOT_PRESENT"]),
        ("Só HAL serial (avançado)","serial_hal_only",         "bool",   None),
    ],
    "localConfig.bluetooth": [
        ("Habilitado",              "enabled",                 "bool",   None),
        ("Modo de emparelhamento",  "mode",                    "combo",
         ["RANDOM_PIN","FIXED_PIN","NO_PIN"]),
        ("PIN fixo",                "fixed_pin",               "spin_int",(0,999999)),
    ],
    "moduleConfig.mqtt": [
        # Campos do proto MQTTConfig (module_config.proto)
        ("Habilitado",              "enabled",                 "bool",   None),
        ("Servidor",                "address",                 "text",   None),
        ("Utilizador",              "username",                "text",   None),
        ("Senha",                   "password",                "text",   None),
        ("Encriptação habilitada",  "encryption_enabled",      "bool",   None),
        ("JSON habilitado",         "json_enabled",            "bool",   None),
        ("TLS habilitado",          "tls_enabled",             "bool",   None),
        ("Root topic",              "root",                    "text",   None),
        # proxy_to_client_enabled: NÃO é um campo de configuração normal.
        # Quando activo, o firmware envia pacotes mqttClientProxyMessage pelo
        # canal API (TCP/Serial) esperando que o cliente ligado faça o relay
        # para o broker MQTT. O MeshDeck não implementa este protocolo de proxy
        # (previsto para versão futura). Mostrado como read-only com nota.
        ("Proxy para cliente ⚠",   "proxy_to_client_enabled", "readonly_with_note", None),
        ("Relatório para o mapa",   "map_reporting_enabled",   "bool",   None),
        ("Intervalo relatório mapa (s)","map_report_settings.publish_interval_secs","spin_int",(0,86400)),
        ("Precisão posição no mapa (bits)","map_report_settings.position_precision","spin_int",(0,32)),
        ("Reportar localização no mapa","map_report_settings.should_report_location","bool",None),
    ],
    "moduleConfig.serial": [
        ("Habilitado",              "enabled",                 "bool",   None),
        ("Echo",                    "echo",                    "bool",   None),
        ("Baud rate",               "baud",                    "combo",
         ["BAUD_DEFAULT","BAUD_110","BAUD_300","BAUD_600","BAUD_1200",
          "BAUD_2400","BAUD_4800","BAUD_9600","BAUD_19200","BAUD_38400",
          "BAUD_57600","BAUD_115200","BAUD_230400","BAUD_460800",
          "BAUD_576000","BAUD_921600"]),
        ("Timeout (ms)",            "timeout",                 "spin_int",(0,60000)),
        ("Modo",                    "mode",                    "combo",
         ["DEFAULT","SIMPLE","PROTO","TEXTMSG","NMEA","CALTOPO",
          "WS85","VE_DIRECT","MS_CONFIG","LOG","LOGTEXT"]),
        ("RX GPIO",                 "rxd",                     "spin_int",(0,39)),
        ("TX GPIO",                 "txd",                     "spin_int",(0,39)),
        ("Somente RX",              "override_console_serial_port","bool",None),
    ],
    "moduleConfig.externalNotification": [
        ("Habilitado",              "enabled",                 "bool",   None),
        ("Saída activa (ms)",       "output_ms",               "spin_int",(0,60000)),
        ("Saída GPIO",              "output",                  "spin_int",(0,39)),
        ("Saída vibra GPIO",        "output_vibra",            "spin_int",(0,39)),
        ("Saída buzzer GPIO",       "output_buzzer",           "spin_int",(0,39)),
        ("Alerta para mensagem",    "alert_message",           "bool",   None),
        ("Alerta msg pulso",        "alert_message_buzzer",    "bool",   None),
        ("Alerta msg vibra",        "alert_message_vibra",     "bool",   None),
        ("Alerta para bell",        "alert_bell",              "bool",   None),
        ("Alerta bell buzzer",      "alert_bell_buzzer",       "bool",   None),
        ("Alerta bell vibra",       "alert_bell_vibra",        "bool",   None),
        ("Usar PWM buzzer",         "use_pwm",                 "bool",   None),
        ("Nível activo GPIO",       "active",                  "bool",   None),
        ("Repetir alerta durante (s)","nag_timeout",           "spin_int",(0,3600)),
        ("Usar I2S como buzzer",    "use_i2s_as_buzzer",       "bool",   None),
    ],
    "moduleConfig.storeForward": [
        # Campos do proto StoreForwardConfig
        ("Habilitado",              "enabled",                 "bool",   None),
        ("Heartbeat",               "heartbeat",               "bool",   None),
        ("Num records",             "records",                 "spin_int",(0,300)),
        ("Histórico (s)",           "history_return_window",   "spin_int",(0,86400)),
        ("Max msgs histórico",      "history_return_max",      "spin_int",(0,300)),
        ("Servidor S&F",            "is_server",               "bool",   None),
    ],
    "moduleConfig.rangeTest": [
        ("Habilitado",              "enabled",                 "bool",   None),
        ("Intervalo sender (s)",    "sender",                  "spin_int",(0,86400)),
        ("Guardar em CSV",          "save",                    "bool",   None),
        ("Limpar ao reiniciar",     "clear_on_reboot",         "bool",   None),
    ],
    "moduleConfig.telemetry": [
        # Campos do proto TelemetryConfig
        ("Intervalo dispositivo (s)","device_update_interval", "spin_int",(0,86400)),
        ("Intervalo ambiente (s)",  "environment_update_interval","spin_int",(0,86400)),
        ("Medição ambiente activa", "environment_measurement_enabled","bool",None),
        ("Ambiente no ecrã",        "environment_screen_enabled","bool",  None),
        ("Temperatura em Fahrenheit","environment_display_fahrenheit","bool",None),
        ("Air quality activo",      "air_quality_enabled",     "bool",   None),
        ("Intervalo air quality (s)","air_quality_interval",   "spin_int",(0,86400)),
        ("Medição potência activa", "power_measurement_enabled","bool",  None),
        ("Intervalo potência (s)",  "power_update_interval",   "spin_int",(0,86400)),
        ("Potência no ecrã",        "power_screen_enabled",    "bool",   None),
        ("Telemetria do dispositivo","device_telemetry_enabled","bool",  None),
        ("Air quality no ecrã",     "air_quality_screen_enabled","bool", None),
        ("Medição de saúde activa", "health_measurement_enabled","bool", None),
        ("Intervalo saúde (s)",     "health_update_interval",  "spin_int",(0,86400)),
        ("Saúde no ecrã",           "health_screen_enabled",   "bool",   None),
    ],
    "moduleConfig.cannedMessage": [
        # ── Campo especial: lista de mensagens (separadas por |, max 200 chars total) ──
        # Guardado via localNode.setCannedMessages(), não via writeConfig
        ("Mensagens pré-definidas", "__canned_messages__",     "canned_messages", None),
        # ── Configuração do módulo ──────────────────────────────────────────────────
        ("Habilitado",              "enabled",                 "bool",   None),
        ("Enviar sinal Bell",       "send_bell",               "bool",   None),
        ("Fonte de entrada aceite", "allow_input_source",      "text",   None),
        # Rotary encoder #1
        ("Rotary encoder #1",       "rotary1_enabled",         "bool",   None),
        ("Up/Down encoder",         "updown1_enabled",         "bool",   None),
        ("GPIO encoder A",          "inputbroker_pin_a",       "spin_int",(0,39)),
        ("GPIO encoder B",          "inputbroker_pin_b",       "spin_int",(0,39)),
        ("GPIO encoder Press",      "inputbroker_pin_press",   "spin_int",(0,39)),
        # InputEventChar proto: NONE=0,UP=17,DOWN=18,LEFT=19,RIGHT=20,SELECT=10,BACK=27,CANCEL=24
        # FN_* e NUMPAD_* não existem no proto CannedMessageConfig.InputEventChar
        ("Evento CW (cima)",        "inputbroker_event_cw",    "combo",
         ["NONE","UP","DOWN","LEFT","RIGHT","SELECT","BACK","CANCEL"]),
        ("Evento CCW (baixo)",      "inputbroker_event_ccw",   "combo",
         ["NONE","UP","DOWN","LEFT","RIGHT","SELECT","BACK","CANCEL"]),
        ("Evento Press",            "inputbroker_event_press", "combo",
         ["NONE","UP","DOWN","LEFT","RIGHT","SELECT","BACK","CANCEL"]),
    ],
    "moduleConfig.audio": [
        ("Codec2 habilitado",       "codec2_enabled",          "bool",   None),
        ("GPIO PTT",                "ptt_pin",                 "spin_int",(0,39)),
        ("Modo codec2",             "bitrate",                 "combo",
         ["DEFAULT","CODEC2_3200","CODEC2_2400","CODEC2_1600","CODEC2_1400",
          "CODEC2_1300","CODEC2_1200","CODEC2_700","CODEC2_700B"]),
        ("I2S WS GPIO",             "i2s_ws",                  "spin_int",(0,39)),
        ("I2S SD GPIO",             "i2s_sd",                  "spin_int",(0,39)),
        ("I2S DIN GPIO",            "i2s_din",                 "spin_int",(0,39)),
        ("I2S SCK GPIO",            "i2s_sck",                 "spin_int",(0,39)),
    ],
    "moduleConfig.remotehardware": [
        ("Habilitado",              "enabled",                 "bool",   None),
        ("Permitir input não seg.", "allow_undefined_pin_access","bool", None),
    ],
    "moduleConfig.neighborInfo": [
        # Campos do proto NeighborInfoConfig
        ("Habilitado",              "enabled",                 "bool",   None),
        ("Intervalo update (s)",    "update_interval",         "spin_int",(0,86400)),
        ("Transmitir por LoRa",     "transmit_over_lora",      "bool",   None),
    ],
    "moduleConfig.ambientLighting": [
        ("Habilitado LED",          "led_state",               "bool",   None),
        ("Corrente (mA)",           "current",                 "spin_int",(0,31)),
        ("Red",                     "red",                     "spin_int",(0,255)),
        ("Green",                   "green",                   "spin_int",(0,255)),
        ("Blue",                    "blue",                    "spin_int",(0,255)),
    ],
    "moduleConfig.detectionSensor": [
        ("Habilitado",              "enabled",                 "bool",   None),
        ("Intervalo mínimo send (s)","minimum_broadcast_secs", "spin_int",(0,86400)),
        ("Intervalo estado (s)",    "state_broadcast_secs",    "spin_int",(0,86400)),
        ("Usar pull-up",            "use_pullup",              "bool",   None),
        ("Nome",                    "name",                    "text",   None),
        ("Monitor GPIO",            "monitor_pin",             "spin_int",(0,39)),
        ("Tipo de detecção",        "detection_trigger_type",  "combo",
         ["LOGIC_LOW","LOGIC_HIGH","FALLING_EDGE","RISING_EDGE",
          "EITHER_EDGE_ACTIVE_LOW","EITHER_EDGE_ACTIVE_HIGH"]),
        ("Enviar sinal Bell",       "send_bell",               "bool",   None),
    ],
    "moduleConfig.paxcounter": [
        ("Habilitado",              "enabled",                 "bool",   None),
        ("Intervalo Paxcount (s)",  "paxcounter_update_interval","spin_int",(0,86400)),
        ("Limiar RSSI WiFi (dBm)",  "wifi_threshold",          "spin_int",(-128,0)),
        ("Limiar RSSI BLE (dBm)",   "ble_threshold",           "spin_int",(-128,0)),
    ],
    "localConfig.security": [
        ("Chave pública (readonly)","public_key",              "readonly",None),
        ("Admin via canal legacy",  "admin_channel_enabled",   "bool",   None),
        ("Managed mode",            "is_managed",              "bool",   None),
        ("Modo serial (debug)",     "serial_enabled",          "bool",   None),
        ("Log debug via serial",    "debug_log_api_enabled",   "bool",   None),
        # private_key e admin_key ficam de fora de propósito: a chave privada
        # nunca deve ir para o browser.
    ],
    # Módulos novos do firmware 2.7.
    "moduleConfig.trafficManagement": [
        ("Habilitado",              "enabled",                 "bool",   None),
        ("Eliminar posições duplicadas","position_dedup_enabled","bool", None),
        ("Precisão posição (bits)", "position_precision_bits", "spin_int",(0,32)),
        ("Intervalo mínimo posição (s)","position_min_interval_secs","spin_int",(0,86400)),
        ("Responder NodeInfo directamente","nodeinfo_direct_response","bool",None),
        ("Hops máx. resposta NodeInfo","nodeinfo_direct_response_max_hops","spin_int",(0,7)),
        ("Limitar taxa",            "rate_limit_enabled",      "bool",   None),
        ("Janela do limite (s)",    "rate_limit_window_secs",  "spin_int",(0,3600)),
        ("Máx. pacotes na janela",  "rate_limit_max_packets",  "spin_int",(0,1000)),
        ("Descartar desconhecidos", "drop_unknown_enabled",    "bool",   None),
        ("Limiar de desconhecidos", "unknown_packet_threshold","spin_int",(0,1000)),
        ("Esgotar hops telemetria", "exhaust_hop_telemetry",   "bool",   None),
        ("Esgotar hops posição",    "exhaust_hop_position",    "bool",   None),
        ("Router preserva hops",    "router_preserve_hops",    "bool",   None),
    ],
    "moduleConfig.statusmessage": [
        ("Mensagem de estado",      "node_status",             "text",   None),
    ],
    "moduleConfig.tak": [
        ("Equipa (cor)",            "team",                    "combo",
         ["Unspecifed_Color","White","Yellow","Orange","Magenta","Red","Maroon",
          "Purple","Dark_Blue","Blue","Cyan","Teal","Green","Dark_Green","Brown"]),
        ("Papel TAK",               "role",                    "combo",
         ["Unspecifed","TeamMember","TeamLead","HQ","Sniper","Medic",
          "ForwardObserver","RTO","K9"]),
    ],
}

def get_section_label(key: str) -> str:
    """Returns the translated section label for the given config key."""
    _map = {
        "localConfig.device":              "💻 Dispositivo",
        "localConfig.position":            "📍 Posição / GPS",
        "localConfig.power":               "🔋 Energia",
        "localConfig.network":             "🌐 Rede / WiFi",
        "localConfig.display":             "🖥 Display",
        "localConfig.lora":                "📡 LoRa",
        "localConfig.bluetooth":           "🔵 Bluetooth",
        "moduleConfig.mqtt":               "☁ MQTT",
        "moduleConfig.serial":             "🔌 Serial",
        "moduleConfig.externalNotification": "🔔 Notif. Externa",
        "moduleConfig.storeForward":       "📦 Store & Forward",
        "moduleConfig.rangeTest":          "📏 Range Test",
        "moduleConfig.telemetry":          "📊 Telemetria",
        "moduleConfig.cannedMessage":      "💬 Msgs Pre-definidas",
        "moduleConfig.audio":              "🎙 Audio / Codec2",
        "moduleConfig.remotehardware":     "🔧 Hardware Remoto",
        "moduleConfig.neighborInfo":       "🔗 Neighbor Info",
        "moduleConfig.ambientLighting":    "💡 Ilum. Ambiente",
        "moduleConfig.detectionSensor":    "🔍 Sensor Deteccao",
        "moduleConfig.paxcounter":         "🧮 Paxcounter",
        "localConfig.security":            "🔐 Segurança",
        "moduleConfig.trafficManagement":  "🚦 Gestão de Tráfego",
        "moduleConfig.statusmessage":      "📝 Mensagem de Estado",
        "moduleConfig.tak":                "🎖 TAK",
    }
    return tr(_map.get(key, key))


SECTION_WRITE_NAME = {
    "localConfig.device":              "device",
    "localConfig.position":            "position",
    "localConfig.power":               "power",
    "localConfig.network":             "network",
    "localConfig.display":             "display",
    "localConfig.lora":                "lora",
    "localConfig.bluetooth":           "bluetooth",
    "moduleConfig.mqtt":               "mqtt",
    "moduleConfig.serial":             "serial",
    "moduleConfig.externalNotification":"external_notification",
    "moduleConfig.storeForward":       "store_forward",
    "moduleConfig.rangeTest":          "range_test",
    "moduleConfig.telemetry":          "telemetry",
    "moduleConfig.cannedMessage":      "canned_message",
    "moduleConfig.audio":              "audio",
    "moduleConfig.remotehardware":     "remote_hardware",
    "moduleConfig.neighborInfo":       "neighbor_info",
    "moduleConfig.ambientLighting":    "ambient_lighting",
    "moduleConfig.detectionSensor":    "detection_sensor",
    "moduleConfig.paxcounter":         "paxcounter",
    "localConfig.security":            "security",
    "moduleConfig.trafficManagement":  "traffic_management",
    "moduleConfig.statusmessage":      "statusmessage",
    "moduleConfig.tak":                "tak",
}

# Mapeamento explícito sec_key → nome do atributo real no objecto cfg_root.
# A biblioteca meshtastic-python usa nomes que nem sempre correspondem ao
# camelCase da chave nem ao snake_case do write_name.  Este mapa é a fonte
# de verdade e evita toda a lógica de tentativa-e-erro.
SECTION_ATTR_NAME = {
    "localConfig.device":                "device",
    "localConfig.position":              "position",
    "localConfig.power":                 "power",
    "localConfig.network":               "network",
    "localConfig.display":               "display",
    "localConfig.lora":                  "lora",
    "localConfig.bluetooth":             "bluetooth",
    "moduleConfig.mqtt":                 "mqtt",
    "moduleConfig.serial":               "serial",
    "moduleConfig.externalNotification": "externalNotification",
    "moduleConfig.storeForward":         "storeForward",
    "moduleConfig.rangeTest":            "rangeTest",
    "moduleConfig.telemetry":            "telemetry",
    "moduleConfig.cannedMessage":        "cannedMessage",
    "moduleConfig.audio":                "audio",
    "moduleConfig.remotehardware":       "remoteHardware",
    "moduleConfig.neighborInfo":         "neighborInfo",
    "moduleConfig.ambientLighting":      "ambientLighting",
    "moduleConfig.detectionSensor":      "detectionSensor",
    "moduleConfig.paxcounter":           "paxcounter",
    "localConfig.security":              "security",
    "moduleConfig.trafficManagement":    "traffic_management",
    "moduleConfig.statusmessage":        "statusmessage",
    "moduleConfig.tak":                  "tak",
}


def _resolve_sub_obj(cfg_root, sec_key: str):
    """
    Resolve o sub-objecto de configuração para `sec_key` em `cfg_root`.

    Estratégia (em ordem de prioridade):
      1. SECTION_ATTR_NAME — mapeamento explícito (fonte de verdade).
      2. sub_attr camelCase directo (ex: "device", "lora").
      3. snake_case derivado do camelCase (ex: "externalNotification" → "external_notification").
      4. write_name de SECTION_WRITE_NAME (ex: "external_notification").

    Retorna o objecto ou None se nenhuma tentativa funcionar.
    Regista em DEBUG qual estratégia foi bem-sucedida para facilitar diagnóstico.
    """
    _, sub_attr = sec_key.split('.', 1)

    # 1. Mapeamento explícito
    explicit = SECTION_ATTR_NAME.get(sec_key)
    if explicit:
        obj = getattr(cfg_root, explicit, None)
        if obj is not None:
            logger.debug(f"_resolve_sub_obj({sec_key}): explicit attr '{explicit}' ✅")
            return obj
        logger.debug(f"_resolve_sub_obj({sec_key}): explicit attr '{explicit}' not found, falling back")

    # 2. camelCase directo
    obj = getattr(cfg_root, sub_attr, None)
    if obj is not None:
        logger.debug(f"_resolve_sub_obj({sec_key}): camelCase '{sub_attr}' ✅")
        return obj

    # 3. snake_case
    snake = re.sub(r'(?<!^)(?=[A-Z])', '_', sub_attr).lower()
    if snake != sub_attr:
        obj = getattr(cfg_root, snake, None)
        if obj is not None:
            logger.debug(f"_resolve_sub_obj({sec_key}): snake_case '{snake}' ✅")
            return obj

    # 4. write_name
    write_name = SECTION_WRITE_NAME.get(sec_key)
    if write_name and write_name not in (sub_attr, snake):
        obj = getattr(cfg_root, write_name, None)
        if obj is not None:
            logger.debug(f"_resolve_sub_obj({sec_key}): write_name '{write_name}' ✅")
            return obj

    logger.warning(f"_resolve_sub_obj({sec_key}): could not resolve sub-object "
                   f"(tried: {explicit!r}, {sub_attr!r}, {snake!r}, {write_name!r})")
    return None

