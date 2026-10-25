from flask import Flask, render_template

app = Flask(__name__)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/about")
def about():
    return render_template("about.html")

@app.route("/engagements")
def engagements():
    return render_template("engagements.html")

@app.route("/publications")
def publications():
    return render_template("publications.html")

@app.route("/academic")
def academic():
    return render_template("academic.html")

@app.route("/gallery")
def gallery():
    return render_template("gallery.html")

@app.route("/contact")
def contact():
    return render_template("contact.html")

if __name__ == "__main__":
    app.run(debug=True)
