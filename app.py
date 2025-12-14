# app3.py
from flask import Flask, render_template, request, jsonify, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from uuid import uuid4
from datetime import datetime

# NLP parser (regex + ML for priority/category/due_date)
from task_parsing import parse_add_task_command

# Optional: intent model (for inspection / future extensions)
try:
    from joblib import load as joblib_load
except ImportError:
    joblib_load = None

INTENT_MODEL = None
if joblib_load is not None:
    try:
        INTENT_MODEL = joblib_load("intent_model.joblib")
        print("Loaded intent_model.joblib in app3.py")
    except Exception as e:
        print("Could not load intent_model.joblib:", e)
        INTENT_MODEL = None

app = Flask(__name__)

# --- Database setup ---
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///tasks.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)


# --- Model ---
class Task(db.Model):
    """
    Persistent representation of a single task.
    """
    id = db.Column(db.String(36), primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    done = db.Column(db.Boolean, default=False)
    # priority: 1 = low, 2 = medium, 3 = high
    priority = db.Column(db.Integer, default=1)
    category = db.Column(db.String(100), default="general")
    due_date = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        """
        Convert the task to a plain dictionary suitable for JSON.
        """
        return {
            "id": self.id,
            "name": self.name,
            "done": self.done,
            "priority": self.priority,
            "category": self.category,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "created_at": self.created_at.isoformat()
        }


# --- Routes ---
@app.route('/')
def home():
    """
    Render the main UI.
    index.html is the front-end template.
    """
    return render_template('index.html')


@app.route('/tasks', methods=['GET'])
def get_tasks():
    """
    Return tasks as JSON, with optional filter and sorting.
    Called by script.js -> refreshTasks().
    """
    done_filter = request.args.get('done')
    sort_by = request.args.get('sort', 'created')

    q = Task.query

    if done_filter == "true":
        q = q.filter_by(done=True)
    elif done_filter == "false":
        q = q.filter_by(done=False)

    if sort_by == 'priority':
        q = q.order_by(Task.priority.desc())
    elif sort_by == 'due':
        # tasks with NULL due_date will show last; that is acceptable for now
        q = q.order_by(Task.due_date.asc())
    elif sort_by == 'category':
        q = q.order_by(Task.category.asc())
    else:
        q = q.order_by(Task.created_at.asc())

    tasks = q.all()
    return jsonify([t.to_dict() for t in tasks])


@app.route('/add', methods=['POST'])
def add_task():
    """
    Main entry for creating tasks.

    Called by:
      - script.js processCommand() when the user says "add ..." / "remind me to ..."
      - manual form (typed input)

    Uses NLP + ML via parse_add_task_command().
    """
    data = request.get_json() or {}
    task_text = (data.get('task') or "").strip()
    if not task_text:
        return jsonify({'error': 'No task name provided'}), 400

    # Optional: inspect intent with ML model (for debugging / future features).
    # No validation is performed here; the frontend already routes commands.
    if INTENT_MODEL is not None:
        try:
            intent = INTENT_MODEL.predict([task_text])[0]
            print(f"intent_model predicted: {intent} for text: {task_text!r}")
        except Exception as e:
            print("intent_model prediction failed:", e)

    # Use the hybrid NLP parser (regex + ML) to get name, priority, category, due_date
    parsed = parse_add_task_command(task_text)

    if not parsed["name"]:
        return jsonify({'error': 'Empty task name after parsing'}), 400

    # Prevent duplicate names (case-insensitive)
    existing = Task.query.filter(Task.name.ilike(parsed["name"])).first()
    if existing:
        return jsonify({'error': 'Task already exists'}), 409

    new_task = Task(
        id=str(uuid4()),
        name=parsed["name"],
        priority=parsed["priority"],
        category=parsed["category"],
        due_date=parsed["due_date"]
    )
    db.session.add(new_task)
    db.session.commit()

    # Build a friendly message for text-to-speech
    msg = f"Task '{parsed['name']}' added"
    # Map 1, 2, 3 -> low/medium/high for the response sentence
    priority_words = {1: "low", 2: "medium", 3: "high"}
    if parsed['priority'] in priority_words:
        msg += f" with {priority_words[parsed['priority']]} priority"
    if parsed['due_date']:
        msg += f" due {parsed['due_date'].strftime('%b %d, %Y')}"

    return jsonify({'message': msg, 'task': new_task.to_dict()}), 201


@app.route('/mark-by-name', methods=['POST'])
def mark_task_by_name():
    """
    Mark a task as done by name.

    Called by script.js when the user says:
      "mark buy milk as done"
    """
    data = request.get_json() or {}
    name = (data.get('name') or "").strip()
    if not name:
        return jsonify({'error': 'No task name provided'}), 400

    t = Task.query.filter(Task.name.ilike(name)).first()
    if not t:
        return jsonify({'error': f"Task '{name}' not found"}), 404

    t.done = True
    db.session.commit()
    return jsonify({'message': f"Marked {t.name} as done", 'task': t.to_dict()})


@app.route('/delete-by-name', methods=['POST'])
def delete_task_by_name():
    """
    Delete a task by name.

    Called by script.js when the user says:
      "delete buy milk" / "remove finish my homework"
    """
    data = request.get_json() or {}
    name = (data.get('name') or "").strip()
    if not name:
        return jsonify({'error': 'No task name provided'}), 400

    t = Task.query.filter(Task.name.ilike(name)).first()
    if not t:
        return jsonify({'error': f"Task '{name}' not found"}), 404

    db.session.delete(t)
    db.session.commit()
    return jsonify({'message': f"Deleted {t.name}"})


@app.route('/toggle', methods=['POST'])
def toggle_task():
    """
    Toggle done/undone by ID (used by checkbox click in the UI).
    """
    data = request.get_json() or {}
    task_id = data.get('id')
    if not task_id:
        return jsonify({'error': 'No task id provided'}), 400

    t = Task.query.get(task_id)
    if not t:
        return jsonify({'error': 'Task not found'}), 404

    t.done = not t.done
    db.session.commit()

    message = f"Marked {t.name} as done" if t.done else f"Marked {t.name} as undone"
    return jsonify({'message': message, 'task': t.to_dict()})


@app.route('/delete', methods=['POST'])
def delete_task():
    """
    Delete a task by ID (used by the Delete button in the UI).
    """
    data = request.get_json() or {}
    task_id = data.get('id')
    if not task_id:
        return jsonify({'error': 'No task id provided'}), 400

    t = Task.query.get(task_id)
    if not t:
        return jsonify({'error': 'Task not found'}), 404

    db.session.delete(t)
    db.session.commit()
    return jsonify({'message': f"Deleted {t.name}"})


@app.route('/clear-completed', methods=['POST'])
def clear_completed():
    """
    Clear only completed tasks.
    """
    try:
        Task.query.filter_by(done=True).delete()
        db.session.commit()
        return jsonify({'message': 'Completed tasks cleared'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/clear', methods=['POST'])
def clear_all():
    """
    Clear all tasks.
    """
    Task.query.delete()
    db.session.commit()
    return jsonify({'message': 'All tasks cleared'})


# --- Service worker from /sw.js (controls '/') ---
@app.route('/sw.js')
def service_worker():
    """
    Serve the service worker script from the static directory.
    """
    return send_from_directory('static', 'sw.js', mimetype='application/javascript')


# --- Initialize DB ---
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    # host='0.0.0.0' lets a phone on the same network reach it over Wi-Fi
    app.run(host='0.0.0.0', port=5000, debug=True)
