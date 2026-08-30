from app.storage import GatewayStore


def test_session_and_message_deduplication(mock_settings):
    store = GatewayStore(mock_settings.state_db)
    try:
        session = store.get_session("user-a")
        assert session.status == "idle"
        assert session.thread_id is None

        store.set_thread("user-a", "thread-1")
        store.set_status("user-a", "analyzing", "reading files")
        store.set_active_turn("user-a", "turn-1")
        updated = store.get_session("user-a")
        assert updated.thread_id == "thread-1"
        assert updated.active_turn_id == "turn-1"
        assert updated.status_detail == "reading files"

        assert store.record_message("user-a", "incoming", "hello", "msg-1")
        assert not store.record_message("user-a", "incoming", "hello", "msg-1")
        assert store.record_message("user-a", "outgoing", "reply")
    finally:
        store.close()

