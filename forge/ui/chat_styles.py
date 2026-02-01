"""
CSS and JavaScript for the AI chat widget.

These are kept separate for readability and to reduce ai_chat_widget.py size.
"""

from forge.ui.tool_rendering import get_diff_styles


def get_chat_styles() -> str:
    """Return CSS styles for the chat display."""
    return (
        get_diff_styles()
        + """
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            padding: 20px;
            background: #ffffff;
            margin: 0;
        }
        #messages-container {
            /* Container for all messages - content is injected here */
        }
        .message {
            margin-bottom: 20px;
            padding: 15px;
            border-radius: 8px;
        }
        .user {
            background: #e3f2fd;
            margin-left: 20%;
        }
        .assistant {
            background: #f5f5f5;
            margin-right: 20%;
        }
        .system {
            background: #fff3cd;
            border: 2px solid #ffc107;
            margin: 0 10%;
        }
        .role {
            font-weight: bold;
            margin-bottom: 8px;
            color: #666;
        }
        code {
            background: #f0f0f0;
            padding: 2px 6px;
            border-radius: 3px;
            font-family: "Courier New", monospace;
        }
        pre {
            background: #f0f0f0;
            padding: 12px;
            border-radius: 6px;
            overflow-x: auto;
        }
        /* Streaming content shows as preformatted until finalized */
        #streaming-message .content {
            white-space: pre-wrap;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        }
        /* Streaming tool calls */
        .streaming-tools {
            margin-top: 12px;
            border-top: 1px solid #ddd;
            padding-top: 12px;
        }
        .streaming-tool-call {
            margin-bottom: 10px;
        }
        .streaming-tool-call .tool-name {
            font-weight: bold;
            color: #1976d2;
            font-size: 14px;
        }
        .streaming-tool-call .tool-args {
            background: #f5f5f5;
            border: 1px solid #e0e0e0;
            border-radius: 4px;
            padding: 8px 12px;
            margin-top: 6px;
            font-family: "Courier New", monospace;
            font-size: 12px;
            white-space: pre-wrap;
            word-break: break-all;
            max-height: 200px;
            overflow-y: auto;
        }
        .streaming-tool-call .cursor {
            animation: blink 1s step-end infinite;
            color: #1976d2;
        }
        @keyframes blink {
            0%, 100% { opacity: 1; }
            50% { opacity: 0; }
        }
        .approval-buttons {
            margin-top: 10px;
            display: flex;
            gap: 10px;
        }
        .approval-buttons button {
            padding: 8px 16px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 14px;
            font-weight: bold;
        }
        .approval-buttons button:first-child {
            background: #4caf50;
            color: white;
        }
        .approval-buttons button:first-child:hover {
            background: #45a049;
        }
        .approval-buttons button:last-child {
            background: #f44336;
            color: white;
        }
        .approval-buttons button:last-child:hover {
            background: #da190b;
        }
        .approval-buttons button:disabled {
            opacity: 0.5;
            cursor: not-allowed;
        }
        /* Turn wrapper and actions */
        .turn {
            position: relative;
            margin-bottom: 8px;
            padding-left: 24px;  /* Fixed space for turn marker */
        }
        .turn-marker {
            position: absolute;
            left: 0;
            top: 0;
            bottom: 0;
            width: 20px;
            border-left: 3px solid transparent;
            cursor: pointer;
            transition: border-color 0.2s;
        }
        .turn:hover .turn-marker {
            border-left-color: #e0e0e0;
        }
        .turn-marker:hover {
            border-left-color: #2196f3 !important;
        }
        .turn-actions {
            display: flex;
            gap: 8px;
            padding: 4px 0;
            opacity: 0;
            transition: opacity 0.2s;
        }
        .turn-actions-top {
            padding-bottom: 8px;
        }
        .turn-actions-bottom {
            padding-top: 8px;
        }
        .turn:hover .turn-actions {
            opacity: 1;
        }
        .turn-btn {
            background: #f5f5f5;
            border: 1px solid #ddd;
            border-radius: 4px;
            padding: 4px 12px;
            font-size: 12px;
            cursor: pointer;
            transition: background 0.2s;
        }
        .turn-btn:hover {
            background: #e0e0e0;
        }
        .revert-btn:hover {
            background: #ffecb3;
            border-color: #ff9800;
        }
        .fork-btn:hover {
            background: #e3f2fd;
            border-color: #2196f3;
        }
    """
    )


def get_chat_scripts() -> str:
    """Return JavaScript for the chat display."""
    return """
        var bridge;

        // Initialize web channel
        new QWebChannel(qt.webChannelTransport, function(channel) {
            bridge = channel.objects.bridge;
        });

        function approveTool(toolName, buttonElement) {
            // Disable the button immediately
            buttonElement.disabled = true;

            if (bridge) {
                bridge.handleToolApproval(toolName, true);
                // Disable both buttons for this tool
                disableToolButtons(toolName);
            }
        }

        function rejectTool(toolName, buttonElement) {
            // Disable the button immediately
            buttonElement.disabled = true;

            if (bridge) {
                bridge.handleToolApproval(toolName, false);
                // Disable both buttons for this tool
                disableToolButtons(toolName);
            }
        }

        function disableToolButtons(toolName) {
            // Find the button that was clicked and disable both buttons in its container
            var buttons = document.querySelectorAll('.approval-buttons button');
            buttons.forEach(function(btn) {
                var onclick = btn.getAttribute('onclick');
                if (onclick && onclick.includes(toolName)) {
                    // Found a button for this tool - disable its parent container's buttons
                    var container = btn.closest('.approval-buttons');
                    if (container) {
                        var containerButtons = container.querySelectorAll('button');
                        containerButtons.forEach(function(b) {
                            b.disabled = true;
                        });
                    }
                }
            });
        }

        function revertTurn(messageIndex) {
            // Revert THIS turn and all later turns
            if (bridge) {
                bridge.handleRevertTurn(messageIndex);
            }
        }

        function revertToTurn(messageIndex) {
            // Revert TO here (keep this turn, undo later turns)
            if (bridge) {
                bridge.handleRevertToTurn(messageIndex);
            }
        }

        function forkBeforeTurn(messageIndex) {
            // Fork from BEFORE this turn
            if (bridge) {
                bridge.handleForkBeforeTurn(messageIndex);
            }
        }

        function forkAfterTurn(messageIndex) {
            // Fork from AFTER this turn
            if (bridge) {
                bridge.handleForkAfterTurn(messageIndex);
            }
        }

        function scrollTurn(turnIndex) {
            // Click on turn marker scrolls to top/bottom of that turn
            var turn = document.querySelector('.turn[data-turn="' + turnIndex + '"]');
            if (!turn) return;

            var turnRect = turn.getBoundingClientRect();
            var viewportMid = window.innerHeight / 2;

            // If turn top is in bottom half of viewport, scroll to top of turn
            // Otherwise scroll to bottom of turn
            if (turnRect.top > viewportMid) {
                turn.scrollIntoView({ behavior: 'smooth', block: 'start' });
            } else {
                turn.scrollIntoView({ behavior: 'smooth', block: 'end' });
            }
        }

        // Update messages container content (called from Python)
        function updateMessages(html, scrollToBottom) {
            var container = document.getElementById('messages-container');
            if (container) {
                container.innerHTML = html;
                if (scrollToBottom) {
                    window.scrollTo(0, document.body.scrollHeight);
                }
            }
        }
    """