# Project Specification

Act as an expert Full-Stack Python Developer. Create a complete, production-ready Kanban Board web application using Python 3.12, Flask, HTML5, CSS3, and native JavaScript (ES6+). 

The application must include the following architecture and core features:

1. ARCHITECTURE & TECH STACK:
- Backend: Python 3.12 with Flask. Use an in-memory Python dictionary or list to store data so it works out-of-the-box without external database dependencies.
- Frontend: Single-page application (SPA) style using valid HTML5, modern CSS3 (Flexbox/Grid), and vanilla JavaScript.
- Drag-and-Drop: Use the native HTML5 Drag and Drop API (do not use external libraries like jQuery UI or SortableJS).

2. CORE FUNCTIONALITY:
- Frames (Columns):
  * Create new frames (e.g., "To Do", "In Progress", "Done").
  * Update frame titles inline.
  * Delete frames (which also deletes or orphans the cards inside).
  * Reorder/Drag-and-drop frames horizontally to change column order.
- Cards (Tasks):
  * Create new cards inside any specific frame.
  * Update card details (Title, Description, and Tags).
  * Delete cards.
  * Drag-and-drop cards vertically within the same frame, or horizontally between different frames.
- Filtering/Search:
  * A global search bar at the top that filters cards in real-time by Title, Description, or Tags across all frames without reloading the page.

3. DESIGN & UI/UX:
- Professional, clean, modern, and dark-themed UI (resembling Trello or Linear).
- Visually distinct drag-and-drop placeholders or drop-zones.
- Smooth transitions and responsive layout.

4. CODE OUTPUT REQUIREMENTS:
- Provide the complete, production-ready code. Do not use placeholders, placeholders like "// TODO", or truncated snippets.
- Combine the code into a clean, logical file structure:
  * `app.py` (Flask backend, API routes, data store).
  * `templates/index.html` (HTML structure).
  * `static/style.css` (Modern CSS styling).
  * `static/main.js` (State management, API calls, HTML5 drag-and-drop logic).
- Include a brief explanation of how to run the app.
