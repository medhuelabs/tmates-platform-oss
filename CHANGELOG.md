# Changelog

## [Unreleased] - 2025-10-25

### Added - Session Management System

#### Conversation Continuity for Agents

- **Session Management Service**: Automatic session creation and management for agent conversations
- **30-Minute Timeout**: Sessions automatically expire after 30 minutes of inactivity with cleanup
- **Memory Isolation**: Complete conversation context isolation per user/thread/agent combination
- **Database Integration**: Session persistence using PostgreSQL with OpenAI Agents SDK
- **API Integration**: Transparent session handling in chat endpoints and WebSocket notifications
- **Backward Compatibility**: Existing conversations work without sessions (NULL session_id)

#### Technical Implementation

- **SessionManager**: Core service handling session lifecycle (`/app/services/session_manager.py`)
- **Adam Agent Enhancement**: Session-based memory using `SQLAlchemySession` with PostgreSQL
- **Database Schema**: Added `session_id` columns to `chat_messages` and `chat_threads` tables
- **IPv4 Compatibility**: Fixed Docker networking by switching to Supabase session mode pooler
- **Documentation**: Complete session management documentation (`/docs/session-management.md`)

#### Benefits

- **Seamless Continuity**: Users can continue conversations naturally within 30-minute windows
- **Automatic Management**: No manual session handling required from users or developers
- **Scalable Design**: In-memory tracking with future Redis migration path
- **Clean Memory Management**: Automatic session cleanup prevents memory bloat

## [Previous] - 2025-10-12

### Added - Dwight Optimizations

#### Automatic Task Creation

- **Fix**: Dwight now automatically creates its own task when no control task or `DWIGHT_DEFAULT_TASK_ID` is available
- **Benefit**: Ensures every Dwight run has proper tracking and audit trails
- **Task Naming**:
  - Single pass: "Dwight Single Pass"
  - Patrol mode: "Dwight Patrol (every Xs)"

#### Logging Performance Optimizations

- **Fix**: Optimized `_log()` function to skip LLM processing when `feed=False`
- **Fix**: Removed duplicate patrol status messages
- **Benefits**:
  - ~75% reduction in API costs for typical runs
  - Single pass: 6+ LLM calls → 2 LLM calls
  - Patrol mode: No repetitive LLM calls for status messages
  - Total tokens reduced from 1400+ to 300-600 per run

#### Message Categorization

- **Technical messages** (no LLM processing): diagnostics, errors, "no queued tasks found"
- **User-facing messages** (LLM processed): Important status updates, task completion summaries
- **Preserved functionality**: All messages still appear in console and technical logs

### Changed

- Updated documentation in `AGENTS.md`, `README.md`, `manifest.yaml`, and troubleshooting guide
- Removed the legacy Notion settings sync module (`common/settings.py`) and associated documentation/tests

### Performance Impact

- **Cost Savings**: Significant reduction in OpenAI API usage during patrol operations
- **Faster Execution**: Reduced latency from fewer API calls
- **Better UX**: Only essential messages appear in user feeds while maintaining complete technical audit trails

---

_This changelog documents the optimization work done to resolve LLM wastage in Dwight's logging system and ensure proper task tracking._
