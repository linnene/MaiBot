from datetime import datetime

from src.chat.message_receive.message import SessionMessage
from src.common.data_models.mai_message_data_model import MessageInfo, UserInfo
from src.common.data_models.message_component_data_model import MessageSequence, TextComponent
from src.plugin_runtime.hook_payloads import deserialize_session_message, serialize_session_message


def outbound():
    message = SessionMessage(message_id='internal-only', timestamp=datetime(2030, 1, 1), platform='qq')
    message.session_id = 'synthetic-session'
    message.message_info = MessageInfo(user_info=UserInfo('synthetic', 'Synthetic'), group_info=None)
    message.raw_message = MessageSequence([TextComponent('Source link')])
    return message


def test_after_send_hook_exposes_only_actual_platform_receipt():
    message = outbound()
    assert serialize_session_message(message).get('platform_message_id') is None
    message.platform_message_id = 'confirmed-external'
    assert serialize_session_message(message)['platform_message_id'] == 'confirmed-external'


def test_plugin_payload_cannot_forge_confirmed_platform_receipt():
    payload = serialize_session_message(outbound())
    payload['platform_message_id'] = 'untrusted-claim'
    assert deserialize_session_message(payload).platform_message_id is None
