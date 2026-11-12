import sys, threading, tkinter, tkinter.font as tkfont
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

guard = "--guard" in sys.argv
if guard:
    from ui import threading_utils
    threading_utils.install_tk_finalizer_guard()

root = tkinter.Tk()
result = {}

def scenario():
    holder = {"font": tkfont.Font(root=root, size=11)}

    def worker():
        # the LAST reference to a Tk font is dropped on a worker thread, so Font.__del__
        # (a Tcl call) runs there while the main thread, inside a Tk callback, waits for us
        holder.pop("font")

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    result["done"] = True
    if guard:
        from ui import threading_utils
        result["drained"] = threading_utils.drain_deferred_finalizers()
    root.after(50, root.destroy)

root.after(100, scenario)
root.mainloop()
print("COMPLETED", result)
