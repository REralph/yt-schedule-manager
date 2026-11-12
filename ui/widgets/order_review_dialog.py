"""The window where the queue order is reviewed and recorded.

Two modes, one window:
  RECOVER -- no order has been recorded yet. Shows the order recovered from the evidence (scheduling/order_recovery.py):
             every video with its recovered position, its upload time, its current scheduled date/time, a status and
             the evidence for that position -- and, ABOVE the list, every group of videos whose order the evidence cannot
             settle, each with both candidate orders. Nothing can be saved until every group has been decided by you.
  PLACE   -- an order is recorded but some scheduled Shorts have no sequence number and are older than numbered
             ones. You choose how to place them (by upload time, or at the end).
Saving only writes the local order record (data/queue_order.json). It never changes anything on YouTube.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, Optional

import customtkinter as ctk

from data import diagnostics
from data.queue_order import QueueOrderError, QueueOrderStore
from scheduling.order_recovery import (OPT_UPLOAD, STATUS_CERTAIN, STATUS_DECIDED, STATUS_RESOLVED, RecoveryError,
                                       RecoveryReport, registry_items)
from scheduling.queue_registration import MODE_AT_END, MODE_BY_TIME, RegistrationPlan, place_older
from ui.app_state import AppState
from ui.widgets.dev_tag import DevTag

GOOD = "#7fd17f"
BAD = "#e05a5a"
WARN = "#e0a04a"
MUTED = "#888888"
INFO = "#5b9bd5"

UNDECIDED = "undecided"
MEMBER_LINES = 7


def _fmt(t) -> str:
    return t.astimezone().strftime("%d %b %Y %H:%M") if t else "?"


class OrderReviewDialog(ctk.CTkToplevel):
    def __init__(
        self, master, state: AppState, on_saved: Callable[[], None],
        report: Optional[RecoveryReport] = None, placement: Optional[RegistrationPlan] = None, modal: bool = True,
    ):
        super().__init__(master)
        assert (report is None) != (placement is None), "exactly one of report / placement"
        self.app_state = state
        self.report = report
        self.placement = placement
        self._on_saved = on_saved
        self.choice_vars: dict[str, ctk.StringVar] = {}
        self.saved = False
        self.title("Recover the queue order" if report is not None else "Place Shorts that have no sequence number")
        self.geometry("1180x800")
        self.minsize(900, 560)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 4))
        ctk.CTkLabel(head, text=self.title(), font=ctk.CTkFont(size=18, weight="bold")).pack(side="left")
        DevTag(head, state, "0.13.7").show(side="left", padx=(8, 0))
        self.summary_label = ctk.CTkLabel(self, text="", justify="left", anchor="w", wraplength=1100)
        self.summary_label.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 6))

        self.body = ctk.CTkScrollableFrame(self, fg_color="transparent") if report is not None and report.blocks else ctk.CTkFrame(self, fg_color="transparent")
        self.body.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 6))
        self.body.grid_columnconfigure(0, weight=1)

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=3, column=0, sticky="ew", padx=16, pady=(4, 14))
        self.status_label = ctk.CTkLabel(bar, text="", text_color=MUTED, anchor="w", justify="left", wraplength=620)
        self.status_label.pack(side="left", fill="x", expand=True)
        self.save_btn = ctk.CTkButton(bar, text="Save this order", width=160, fg_color="#2f7a3d", hover_color="#256030",
                                      font=ctk.CTkFont(weight="bold"), command=self._save)
        self.save_btn.pack(side="right")
        DevTag(bar, state, "0.13.7.3").show(side="right", padx=(0, 8))
        self.cancel_btn = ctk.CTkButton(bar, text="Close", width=90, fg_color="transparent", border_width=1, command=self.destroy)
        self.cancel_btn.pack(side="right", padx=(0, 8))
        self.copy_btn = ctk.CTkButton(bar, text="Copy report", width=110, fg_color="transparent", border_width=1, command=self._copy_report)
        self.copy_btn.pack(side="right", padx=(0, 8))

        if report is not None:
            self._build_recover()
        else:
            self._build_place()

        if modal:
            self.after(200, self._make_modal)

    def _make_modal(self) -> None:
        try:
            if self.winfo_exists():
                self.transient(self.master.winfo_toplevel())
                self.grab_set()
                self.focus_set()
        except Exception:
            pass  # a window that cannot be made modal still works; it just isn't modal

    # ================================================================= RECOVER mode

    def _build_recover(self) -> None:
        r = self.report
        row = 0
        if r.blocks:
            box = ctk.CTkFrame(self.body, fg_color="#1a1a1a", corner_radius=8)
            box.grid(row=row, column=0, sticky="ew", pady=(0, 8))
            row += 1
            top = ctk.CTkFrame(box, fg_color="transparent")
            top.pack(fill="x", padx=12, pady=(10, 2))
            ctk.CTkLabel(top, text="DECISIONS NEEDED", font=ctk.CTkFont(size=13, weight="bold"), text_color=WARN).pack(side="left")
            DevTag(top, self.app_state, "0.13.7.1").show(side="left", padx=(8, 0))
            ctk.CTkLabel(
                box, anchor="w", justify="left", wraplength=1050, text_color=MUTED,
                text="For these videos the evidence disagrees with itself and no rule can say which is right, so nothing is assumed. "
                "Choose the order for each group. The \"recommended\" answer is the upload order (the order the videos were added) -- "
                "but it is only applied if you choose it.",
            ).pack(fill="x", padx=12, pady=(0, 6))
            self.recommend_btn = ctk.CTkButton(box, text="Choose the recommended answer for every group", width=330, command=self._choose_recommended)
            self.recommend_btn.pack(anchor="w", padx=12, pady=(0, 8))
            for b in r.blocks:
                self._build_block(box, b)
            ctk.CTkFrame(box, height=4, fg_color="transparent").pack()

        tree_wrap = ctk.CTkFrame(self.body, fg_color="transparent")
        tree_wrap.grid(row=row, column=0, sticky="nsew")
        tree_wrap.grid_columnconfigure(0, weight=1)
        tree_wrap.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(tree_wrap, fg_color="transparent")
        head.grid(row=0, column=0, sticky="w", pady=(4, 2))
        ctk.CTkLabel(head, text="RECOVERED ORDER", font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")
        DevTag(head, self.app_state, "0.13.7.2").show(side="left", padx=(8, 0))
        self.tree = self._make_tree(tree_wrap)
        if not r.blocks:
            self.body.grid_rowconfigure(row, weight=1)
            tree_wrap.grid_rowconfigure(1, weight=1)
        self._refresh_recover()

    def _make_tree(self, parent) -> ttk.Treeview:
        style = ttk.Style()
        style.configure("Order.Treeview", background="#151515", fieldbackground="#151515", foreground="#dddddd", rowheight=22, borderwidth=0)
        style.configure("Order.Treeview.Heading", background="#2a2a2a", foreground="#dddddd", relief="flat")
        style.map("Order.Treeview", background=[("selected", "#2f5a8a")], foreground=[("selected", "#ffffff")])
        cols = ("pos", "title", "uploaded", "current", "status", "evidence")
        tree = ttk.Treeview(parent, columns=cols, show="headings", style="Order.Treeview", height=14)
        for col, text, width, stretch in (
            ("pos", "Code", 56, False), ("title", "Video", 330, False), ("uploaded", "Uploaded", 130, False),
            ("current", "Now scheduled", 130, False), ("status", "Status", 82, False), ("evidence", "Evidence for this position", 520, True),
        ):
            tree.heading(col, text=text)
            tree.column(col, width=width, stretch=stretch, anchor="w")
        tree.tag_configure(STATUS_CERTAIN, foreground="#9bd49b")
        tree.tag_configure(STATUS_RESOLVED, foreground="#8fb8e8")
        tree.tag_configure(STATUS_DECIDED, foreground="#e0b060")
        tree.tag_configure(UNDECIDED, foreground="#e07070")
        sb = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.grid(row=1, column=0, sticky="nsew")
        sb.grid(row=1, column=1, sticky="ns")
        return tree

    def _build_block(self, parent, b) -> None:
        r = self.report
        card = ctk.CTkFrame(parent, fg_color="#222222", corner_radius=6)
        card.pack(fill="x", padx=12, pady=4)
        ctk.CTkLabel(card, text=f"Group {b.block_id}  ·  {len(b.members)} videos", font=ctk.CTkFont(weight="bold"), anchor="w").pack(fill="x", padx=10, pady=(8, 0))
        ctk.CTkLabel(card, text=b.reason, anchor="w", justify="left", wraplength=1020, text_color=MUTED).pack(fill="x", padx=10)
        for e in b.evidence[:3]:
            ctk.CTkLabel(card, text="• " + e, anchor="w", justify="left", wraplength=1020, text_color=MUTED, font=ctk.CTkFont(size=11)).pack(fill="x", padx=18)
        members = sorted(b.members, key=lambda v: r.created[v])
        shift: dict[str, str] = {}
        if OPT_UPLOAD in b.options and len(b.options) > 1:
            # list the videos that MOVE the most between the two candidate orders first, so the real outlier is what you see
            up = {v: i for i, v in enumerate(b.options[OPT_UPLOAD].order, start=1)}
            other = next(o for k, o in b.options.items() if k != OPT_UPLOAD)
            sc = {v: i for i, v in enumerate(other.order, start=1)}
            members.sort(key=lambda v: (-abs(up[v] - sc[v]), r.created[v]))
            shift = {v: f"   #{up[v]} by upload -> #{sc[v]} by schedule" for v in members if up[v] != sc[v]}
            head_text = "Videos that move between the two orders (most displaced first); # is the place within this group:"
        else:
            head_text = "Videos in this group:"
        ctk.CTkLabel(card, text=head_text, anchor="w", text_color=MUTED, font=ctk.CTkFont(size=11)).pack(fill="x", padx=10, pady=(4, 0))
        lines = [f"    {r.titles.get(v, v)[:46]:46}  uploaded {_fmt(r.created[v])}  now {_fmt(r.current[v])}{shift.get(v, '')}" for v in members[:MEMBER_LINES]]
        if len(members) > MEMBER_LINES:
            lines.append(f"    ... and {len(members) - MEMBER_LINES} more (all are listed in the recovered order below)")
        ctk.CTkLabel(card, text="\n".join(lines), anchor="w", justify="left", font=ctk.CTkFont(family="Consolas", size=11)).pack(fill="x", padx=10, pady=(4, 2))
        var = ctk.StringVar(value="")
        self.choice_vars[b.block_id] = var
        for key, opt in b.options.items():
            suffix = "   (recommended)" if key == b.recommended else ""
            ctk.CTkRadioButton(card, text=f"Use the {opt.label}{suffix}", variable=var, value=key, command=self._refresh_recover).pack(anchor="w", padx=18, pady=2)
        ctk.CTkFrame(card, height=4, fg_color="transparent").pack()

    def _choices(self) -> dict[str, str]:
        return {bid: v.get() for bid, v in self.choice_vars.items() if v.get()}

    def _choose_recommended(self) -> None:
        for b in self.report.blocks:
            self.choice_vars[b.block_id].set(b.recommended)
        self._refresh_recover()

    def _refresh_recover(self) -> None:
        r = self.report
        choices = self._choices()
        self.summary_label.configure(text="\n".join(r.summary_lines(choices)), text_color="#dddddd")
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        waiting = set(r.missing_choices(choices))
        if r.blocking_issues:
            self.status_label.configure(text="This order cannot be recorded: " + "; ".join(i.message for i in r.blocking_issues[:2]), text_color=BAD)
            self.save_btn.configure(state="disabled")
            return
        provisional = {b.block_id: choices.get(b.block_id) or (OPT_UPLOAD if OPT_UPLOAD in b.options else next(iter(b.options))) for b in r.blocks}
        try:
            items = r.build(provisional)
        except RecoveryError as e:  # cannot happen once the checks above pass; shown rather than hidden if it ever does
            self.status_label.configure(text=f"Could not build the order: {e}", text_color=BAD)
            self.save_btn.configure(state="disabled")
            return
        undecided_members = {m for b in r.blocks if b.block_id in waiting for m in b.members}
        for it in items:
            undecided = it.video_id in undecided_members
            status = UNDECIDED if undecided else it.status
            self.tree.insert("", "end", values=(
                f"{it.position:04d}", it.title[:70], _fmt(it.created_at), _fmt(it.current_at), status.upper(),
                "needs your decision (shown in upload order for now)" if undecided else it.evidence_text.split("; ", 1)[-1] if "; " in it.evidence_text else it.evidence_text,
            ), tags=(status,))
        if waiting:
            self.status_label.configure(text=f"{len(waiting)} group(s) still need your decision before this can be saved.", text_color=WARN)
            self.save_btn.configure(state="disabled")
        else:
            self.status_label.configure(text="Every position is settled. Saving records this order; nothing on YouTube changes.", text_color=GOOD)
            self.save_btn.configure(state="normal")

    # ================================================================= PLACE mode

    def _build_place(self) -> None:
        p = self.placement
        self.mode_var = ctk.StringVar(value="")
        lines = [f"{len(p.older)} scheduled Short(s) have no sequence number but were uploaded BEFORE videos that do, so where they belong is not obvious."]
        if p.no_time:
            lines.append(f"{len(p.no_time)} more have no recorded upload time and cannot be placed at all.")
        self.summary_label.configure(text="\n".join(lines))
        box = ctk.CTkFrame(self.body, fg_color="#1a1a1a", corner_radius=8)
        box.pack(fill="both", expand=True)
        text = "\n".join(
            f"{x.video.title[:60]:60}  uploaded {_fmt(x.created)}  -> after \"{(x.after_title or 'the start of the queue')[:40]}\""
            for x in p.older[:14]
        ) + (f"\n... and {len(p.older) - 14} more" if len(p.older) > 14 else "")
        ctk.CTkLabel(box, text=text, anchor="w", justify="left", font=ctk.CTkFont(family="Consolas", size=11)).pack(fill="x", padx=12, pady=10)
        ctk.CTkRadioButton(box, text="Place each one by its upload time (recommended): after the latest numbered video uploaded before it",
                           variable=self.mode_var, value=MODE_BY_TIME, command=self._refresh_place).pack(anchor="w", padx=12, pady=3)
        ctk.CTkRadioButton(box, text="Put them all at the end of the queue, in upload order",
                           variable=self.mode_var, value=MODE_AT_END, command=self._refresh_place).pack(anchor="w", padx=12, pady=3)
        self._refresh_place()

    def _refresh_place(self) -> None:
        if self.placement.no_time:
            self.status_label.configure(text="Some videos have no upload time, so nothing can be recorded until that is resolved.", text_color=BAD)
            self.save_btn.configure(state="disabled")
        elif not self.mode_var.get():
            self.status_label.configure(text="Choose how to place them.", text_color=WARN)
            self.save_btn.configure(state="disabled")
        else:
            self.status_label.configure(text="Saving records the places; nothing on YouTube changes.", text_color=GOOD)
            self.save_btn.configure(state="normal")

    # ================================================================= actions

    def _copy_report(self) -> None:
        try:
            text = self.report.to_text(self._choices()) if self.report is not None else "\n".join(
                f"{x.video.title}  uploaded {_fmt(x.created)}  after {x.after_title or '(start)'}" for x in self.placement.older)
            self.clipboard_clear()
            self.clipboard_append(text)
            self.status_label.configure(text="Report copied to the clipboard.", text_color=GOOD)
        except Exception as e:
            self.status_label.configure(text=f"Could not copy: {e}", text_color=BAD)

    def _save(self) -> None:
        try:
            if self.report is not None:
                items = self.report.build(self._choices())
                if not messagebox.askyesno(
                    "Record this queue order?",
                    f"This permanently records the order of {len(items)} Short(s) as sequence numbers.\n\n"
                    "Daily Upload Frequency will then follow these numbers and never change the order. "
                    "Nothing on YouTube is changed by this step.\n\nRecord it?", parent=self,
                ):
                    return
                QueueOrderStore.shared().adopt(registry_items(items))
            else:
                n = place_older(QueueOrderStore.shared(), self.placement, self.mode_var.get())
                self.status_label.configure(text=f"Placed {n} Short(s).", text_color=GOOD)
        except (RecoveryError, QueueOrderError) as e:
            messagebox.showerror("Could not record the order", str(e), parent=self)
            return
        except Exception as e:
            diagnostics.report_recovered("Recording the queue order", e, page="Daily Upload Frequency")
            messagebox.showerror("Could not record the order", f"{type(e).__name__}: {e}", parent=self)
            return
        self.saved = True
        try:
            self._on_saved()
        finally:
            self.destroy()
