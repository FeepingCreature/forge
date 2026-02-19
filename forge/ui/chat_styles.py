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
        /* Markdown tables */
        table {
            border-collapse: collapse;
            margin: 12px 0;
            width: 100%;
            font-size: 14px;
            background: #ffffff;
        }
        th, td {
            border: 1px solid #ccc;
            padding: 8px 12px;
            text-align: left;
        }
        th {
            background: #e8e8e8;
            font-weight: 600;
        }
        tr:nth-child(even) td {
            background: #f5f5f5;
        }
        tr:hover td {
            background: #e3f2fd;
        }
        /* Mermaid diagram containers */
        .mermaid-container {
            margin: 12px 0;
            padding: 16px;
            background: #fafafa;
            border-radius: 8px;
            overflow-x: auto;
            text-align: center;
        }
        .mermaid-container svg {
            max-width: 100%;
            height: auto;
        }
        .mermaid-error {
            color: #d32f2f;
            font-size: 12px;
            margin-bottom: 8px;
            padding: 8px;
            background: #ffebee;
            border-radius: 4px;
        }
        /* Streaming mermaid: hide raw code when rendered SVG is shown */
        #streaming-message pre[style*="display: none"] + .mermaid-container {
            margin: 12px 0;
        }
        .streaming-text {
            white-space: pre-wrap;
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

                // Render any Mermaid diagrams in the new content
                renderMermaidDiagrams();

                if (scrollToBottom) {
                    window.scrollTo(0, document.body.scrollHeight);
                }
            }
        }

        // Render Mermaid diagrams - finds code blocks with class 'language-mermaid'
        // and converts them to rendered SVG diagrams.
        // Renders are serialized (one at a time) to prevent mermaid from
        // tripping over concurrent DOM manipulation.
        var _mermaidRenderQueue = [];
        var _mermaidRendering = false;

        function renderMermaidDiagrams() {
            if (typeof mermaid === 'undefined' || !window._mermaidReady) return;

            // Find all mermaid code blocks that haven't been rendered yet
            var codeBlocks = document.querySelectorAll('pre > code.language-mermaid');
            codeBlocks.forEach(function(codeBlock, index) {
                var pre = codeBlock.parentElement;
                // Skip if already processed
                if (pre.dataset.mermaidProcessed) return;
                // Skip streaming blocks (handled by renderStreamingMermaid)
                if (pre.dataset.streaming) return;
                pre.dataset.mermaidProcessed = 'true';

                _mermaidRenderQueue.push({
                    pre: pre,
                    text: codeBlock.textContent,
                    id: 'mermaid-diagram-' + Date.now() + '-' + index
                });
            });

            _processMermaidQueue();
        }

        function _processMermaidQueue() {
            if (_mermaidRendering || _mermaidRenderQueue.length === 0) return;
            _mermaidRendering = true;

            var item = _mermaidRenderQueue.shift();
            var pre = item.pre;
            var container = document.createElement('div');
            container.className = 'mermaid-container';

            // Each render gets its own sandbox element so concurrent
            // cleanup can't null-out nodes mermaid is still using.
            var sandbox = document.createElement('div');
            sandbox.style.cssText = 'position:fixed;left:-9999px;top:-9999px;width:1px;height:1px;overflow:hidden;';
            document.body.appendChild(sandbox);

            try {
                mermaid.render(item.id, item.text, sandbox).then(function(result) {
                    container.innerHTML = result.svg;
                    if (pre.parentNode) pre.replaceWith(container);
                    sandbox.remove();
                    _mermaidRendering = false;
                    _processMermaidQueue();
                }).catch(function(err) {
                    sandbox.remove();
                    // On error, show the original code with an error indicator
                    container.innerHTML = '<div class="mermaid-error">⚠️ Diagram error: ' +
                        (err.message || String(err)) + '</div>';
                    container.appendChild(pre.cloneNode(true));
                    if (pre.parentNode) pre.replaceWith(container);
                    _mermaidRendering = false;
                    _processMermaidQueue();
                });
            } catch (err) {
                // Sync error (e.g., mermaid.render not available)
                console.error('Mermaid render error:', err);
                sandbox.remove();
                _mermaidRendering = false;
                _processMermaidQueue();
            }
        }

        // Render mermaid diagrams in the streaming message.
        // Uses content hashing to avoid re-rendering unchanged diagrams,
        // and renders into a sibling container to prevent flicker.
        //
        // Each render gets its own offscreen sandbox element so that
        // concurrent renders (or sandbox cleanup) can't null-out DOM nodes
        // that mermaid is still working with.
        window._mermaidStreamCounter = 0;

        function _createSandbox() {
            var sb = document.createElement('div');
            sb.style.cssText = 'position:fixed;left:-9999px;top:-9999px;width:1px;height:1px;overflow:hidden;';
            document.body.appendChild(sb);
            return sb;
        }

        function renderStreamingMermaid() {
            if (typeof mermaid === 'undefined' || !window._mermaidReady) return;

            var streamingMsg = document.getElementById('streaming-message');
            if (!streamingMsg) return;

            var codeBlocks = streamingMsg.querySelectorAll('pre > code.language-mermaid');
            codeBlocks.forEach(function(codeBlock) {
                var pre = codeBlock.parentElement;
                var diagramText = codeBlock.textContent.trim();
                if (!diagramText) return;

                // Use a simple hash of the content to detect changes
                var contentHash = diagramText.length + ':' + diagramText.slice(-80);

                // Check if we already have a rendered container for this pre
                var container = pre.nextElementSibling;
                if (container && container.classList.contains('mermaid-container')
                    && container.dataset.contentHash === contentHash) {
                    // Content unchanged - keep existing render
                    return;
                }

                // Content changed or new — render into a new container
                var isStreaming = pre.dataset.streaming === 'true';
                var newContainer = document.createElement('div');
                newContainer.className = 'mermaid-container';
                newContainer.dataset.contentHash = contentHash;
                if (isStreaming) {
                    newContainer.innerHTML = '<div style="color:#999;font-size:12px;">⏳ Rendering diagram...</div>';
                }

                // Insert container after pre (or replace existing)
                if (container && container.classList.contains('mermaid-container')) {
                    container.replaceWith(newContainer);
                } else {
                    pre.after(newContainer);
                }

                // Hide the raw code block while rendered version is shown
                pre.style.display = 'none';

                // Each render gets its own sandbox so concurrent renders
                // don't interfere with each other's DOM nodes.
                var sandbox = _createSandbox();
                window._mermaidStreamCounter++;
                var diagramId = 'mermaid-stream-' + window._mermaidStreamCounter;
                try {
                    mermaid.render(diagramId, diagramText, sandbox).then(function(result) {
                        newContainer.innerHTML = result.svg;
                        if (isStreaming) {
                            newContainer.innerHTML += '<div style="color:#999;font-size:11px;margin-top:4px;">▋ streaming...</div>';
                        }
                        sandbox.remove();
                    }).catch(function(err) {
                        sandbox.remove();
                        // During streaming, parse errors are expected for incomplete diagrams
                        if (isStreaming) {
                            newContainer.innerHTML = '<div style="color:#999;font-size:12px;">⏳ Building diagram...</div>';
                        } else {
                            newContainer.innerHTML = '<div class="mermaid-error">⚠️ ' +
                                (err.message || String(err)) + '</div>';
                            pre.style.display = '';
                        }
                    });
                } catch(err) {
                    console.error('Mermaid streaming render error:', err);
                    sandbox.remove();
                    pre.style.display = '';
                }
            });
        }
    """
