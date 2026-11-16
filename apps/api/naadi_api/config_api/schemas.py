"""`app_config` keys and the pydantic model that validates each one (seed and admin PUT use the same code).

Contract: ledger 20261116T094619-seg05-seed-629618 (decided by seg04-agents in 20261116T100346). The four
agent-platform keys (models, budget, search, generation) are validated by `naadi_api.llm_config`, which the
LLM layer also runs at load time, so there is one validator per key.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, TypeAdapter, field_validator, model_validator

from .. import llm_config

FLAG_NAME = r"^[a-z][a-z0-9_]{0,47}$"
COPY_KEY = r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ── feature_flags ──


class FeatureFlag(_Strict):
    enabled: bool
    audience: Literal["everyone", "admins"] = "everyone"
    everyone_outside_prod: bool = Field(
        default=False, description="Also on for everyone when APP_ENV is not prod (local, staging)"
    )

    def on_for(self, *, is_admin: bool, app_env: str) -> bool:
        if not self.enabled:
            return False
        if self.audience == "everyone" or is_admin:
            return True
        return self.everyone_outside_prod and app_env != "prod"


class FeatureFlags(RootModel[dict[str, FeatureFlag]]):
    @field_validator("root")
    @classmethod
    def _names(cls, v: dict[str, FeatureFlag]) -> dict[str, FeatureFlag]:
        bad = [k for k in v if not re.match(FLAG_NAME, k)]
        if bad:
            raise ValueError(f"invalid flag names: {', '.join(sorted(bad))}")
        return v


# ── ui.nav ──


class NavEntry(_Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    label: str = Field(min_length=1, max_length=40)
    path: str = Field(pattern=r"^/[a-z0-9/_-]*$", max_length=80, description="In-app route path")
    icon: str = Field(pattern=r"^[a-z0-9-]{1,40}$", description="Icon name from the web app's icon set")
    feature: str = Field(pattern=FLAG_NAME, description="Feature flag that must be on for the item to show")


class Nav(RootModel[list[NavEntry]]):
    @model_validator(mode="after")
    def _unique(self) -> Nav:
        ids = [i.id for i in self.root]
        paths = [i.path for i in self.root]
        if len(set(ids)) != len(ids) or len(set(paths)) != len(paths):
            raise ValueError("nav ids and paths must be unique")
        if len(self.root) > 12:
            raise ValueError("at most 12 nav items")
        return self


# ── exam_rules ──


class BlockRules(_Strict):
    min_questions: int = Field(ge=1, le=40)
    max_questions: int = Field(ge=1, le=80)
    default_questions: int = Field(ge=1, le=80)

    @model_validator(mode="after")
    def _order(self) -> BlockRules:
        if not self.min_questions <= self.default_questions <= self.max_questions:
            raise ValueError("need min_questions <= default_questions <= max_questions")
        return self


class ExamFormat(_Strict):
    label: str = Field(min_length=1, max_length=40)
    blocks: int = Field(ge=1, le=12)
    questions_per_block: int = Field(ge=1, le=80)
    minutes_per_block: int = Field(ge=1, le=240)


class ModeRules(_Strict):
    timed: bool
    seconds_per_question: int | None = Field(ge=10, le=600)
    explanations: Literal["after_each_question", "after_block"]
    assistant: bool

    @model_validator(mode="after")
    def _timer(self) -> ModeRules:
        if self.timed != (self.seconds_per_question is not None):
            raise ValueError("a timed mode needs seconds_per_question; an untimed mode must not have one")
        return self


class ExamModes(_Strict):
    tutor: ModeRules
    timed: ModeRules
    exam: ModeRules

    @model_validator(mode="after")
    def _exam_is_strict(self) -> ExamModes:
        # Never reveal answers mid-block or allow the assistant in exam mode (CLAUDE.md).
        if self.exam.assistant or self.exam.explanations != "after_block" or not self.exam.timed:
            raise ValueError("exam mode must be timed, show explanations after the block and keep the assistant off")
        return self


class ExamRules(_Strict):
    block: BlockRules
    diagnostic_questions: int = Field(ge=1, le=80)
    # Timed/exam sessions (S09): one break pool shared by every multi-section session (USMLE day rule), and how
    # long after a section's deadline a last answer in flight is still recorded.
    break_minutes: int = Field(default=45, ge=0, le=240)
    expiry_grace_seconds: int = Field(default=2, ge=0, le=30)
    exams: dict[Literal["step1", "step2ck", "step3"], ExamFormat]
    modes: ExamModes


# ── storage, ui_copy, sources_policy ──


class StorageBuckets(_Strict):
    content_public: str = Field(pattern=r"^[a-z0-9-]{3,63}$")
    content_private: str = Field(pattern=r"^[a-z0-9-]{3,63}$")
    user_uploads: str = Field(pattern=r"^[a-z0-9-]{3,63}$")
    vault: str = Field(pattern=r"^[a-z0-9-]{3,63}$")
    generated_media: str = Field(pattern=r"^[a-z0-9-]{3,63}$")


# Every ui_copy key the web app reads. An admin PUT (and the seed) must keep all of them, so no button or
# heading can ever be blanked; packages/shared/ui-copy.defaults.json carries their shipped values as the web's
# last-resort fallback (generated by tests/config_api/test_ui_copy_defaults.py, drift-checked in CI).
REQUIRED_UI_COPY_KEYS: frozenset[str] = frozenset(
    {
        # ink (S12a)
        "ink.toolbar",
        "ink.toggle_on",
        "ink.toggle_off",
        "ink.tool.pen",
        "ink.tool.marker",
        "ink.tool.eraser",
        "ink.tool.lasso",
        "ink.tool.shapes",
        "ink.tool.text",
        "ink.tool.with_key",
        "ink.colour.ink_group",
        "ink.colour.marker_group",
        "ink.colour.ink_paper",
        "ink.colour.ink_dark",
        "ink.colour.violet",
        "ink.colour.lime",
        "ink.colour.amber",
        "ink.eraser.mode",
        "ink.eraser.stroke",
        "ink.eraser.pixel",
        "ink.undo",
        "ink.undo_hint",
        "ink.redo",
        "ink.redo_hint",
        "ink.clear",
        "ink.clear_title",
        "ink.clear_body",
        "ink.clear_cancel",
        "ink.clear_confirm",
        "ink.select.menu",
        "ink.select.copy",
        "ink.select.delete",
        "ink.select.convert",
        "ink.select.ask",
        "ink.select.drag_hint",
        "ink.select.region_hint",
        "ink.text.new",
        "ink.text.edit",
        "ink.error.too_many",
        "ink.error.no_canvas",
        "ink.error.canvas_memory",
        "ink.gate.title",
        "ink.gate.opening",
        "ink.gate.failed",
        "ink.gate.unreachable",
        "ink.gate.contract",
        "ink.gate.http",
        "ink.gate.not_found",
        "ink.gate.not_found_body",
        "ink.gate.to_system",
        "ink.lab.breadcrumb",
        "ink.lab.intro",
        "ink.lab.features",
        "ink.lab.yes",
        "ink.lab.no",
        "ink.lab.cap.coalesced_name",
        "ink.lab.cap.coalesced",
        "ink.lab.cap.predicted_name",
        "ink.lab.cap.predicted",
        "ink.lab.cap.raw_name",
        "ink.lab.cap.raw",
        "ink.lab.cap.pen_name",
        "ink.lab.cap.pen",
        "ink.lab.cap.pen_detected",
        "ink.lab.cap.pen_missing",
        "ink.lab.cap.finger_name",
        "ink.lab.cap.finger",
        "ink.lab.cap.finger_draws",
        "ink.lab.cap.finger_scrolls",
        "ink.lab.settings",
        "ink.lab.switch_on",
        "ink.lab.switch_off",
        "ink.lab.pen_only",
        "ink.lab.pen_only_hint",
        "ink.lab.predict",
        "ink.lab.slowmo",
        "ink.lab.slowmo_hint",
        "ink.lab.frame_counter",
        "ink.lab.theme_paper",
        "ink.lab.theme_dark",
        "ink.lab.content_text",
        "ink.lab.content_table",
        "ink.lab.page",
        "ink.lab.items_one",
        "ink.lab.items_other",
        "ink.lab.page_label",
        "ink.lab.sample_intro",
        "ink.lab.sample_plain",
        "ink.lab.sample_table",
        "ink.lab.latency",
        "ink.lab.ms",
        "ink.lab.paint_p50",
        "ink.lab.paint_p95",
        "ink.lab.frame_p50",
        "ink.lab.frame_p95",
        "ink.lab.frames",
        "ink.lab.samples",
        "ink.lab.coalesced_batches",
        "ink.lab.predicted_samples",
        "ink.lab.id_mismatch",
        "ink.lab.dropped",
        "ink.lab.reset",
        "ink.lab.protocol",
        "ink.lab.protocol_1",
        "ink.lab.protocol_2",
        "ink.lab.protocol_3",
        "ink.lab.protocol_4",
        "ink.lab.protocol_5",
        "ink.lab.events",
        "ink.lab.events_empty",
        "ink.lab.events_list",
        "ink.lab.ev.strokes",
        "ink.lab.ev.region",
        "ink.lab.ev.lasso_one",
        "ink.lab.ev.lasso_other",
        "ink.lab.ev.convert",
        "ink.lab.ev.ask",
        "ink.lab.ev.ask_region",
        "ink.lab.png_alt",
        "ink.lab.png_caption",
        "ink.lab.stress",
        "ink.lab.stress_build",
        "ink.lab.stress_rebuild",
        "ink.lab.measure",
        "ink.lab.measuring",
        "ink.lab.scroll_result",
        "ink.lab.stress_label",
        "ink.lab.many",
        "ink.lab.many_body",
        "ink.lab.many_mount",
        "ink.lab.budget",
        "ink.lab.many_label",
        # assistant (S04)
        "agent.quota_reset",
        "agent.rate_limited",
        "agent.offline",
        "agent.exam_mode",
        "agent.unavailable",
        "agent.diag.title",
        "agent.diag.runtime_label",
        "agent.diag.runtime_absent",
        "agent.diag.runtime_error_title",
        "agent.diag.runtime_ok_one",
        "agent.diag.runtime_ok_many",
        "agent.diag.runtime_not_installed",
        "agent.diag.runtime_checks_failed",
        "agent.diag.runtime_no_cron_ping",
        "agent.diag.runtime_cron_stale",
        "agent.diag.runtime_unknown",
        "agent.diag.check_graphs_loaded",
        "agent.diag.check_database",
        "agent.diag.check_broker",
        "agent.diag.check_cron_scheduler",
        "agent.diag.selftest_title",
        "agent.diag.selftest_intro",
        "agent.diag.run",
        "agent.diag.running",
        "agent.diag.offline",
        "agent.diag.result",
        "agent.diag.col_check",
        "agent.diag.col_result",
        "agent.diag.row_outcome",
        "agent.diag.row_provider",
        "agent.diag.row_model",
        "agent.diag.row_latency",
        "agent.diag.outcome_ok",
        "agent.diag.outcome_unavailable",
        "agent.diag.unavailable_detail",
        "agent.diag.quota_chip",
        "agent.diag.confirm_question",
        "agent.diag.record",
        "agent.diag.discard",
        "agent.diag.recorded",
        "agent.diag.not_recorded",
        "agent.diag.error_title",
        "agent.diag.error_session",
        "agent.diag.error_unavailable",
        "agent.diag.error_generic",
        "agent.diag.media_title",
        "agent.diag.media_credit",
        "agent.diag.media_source",
        "agent.diag.video_title",
        "agent.diag.not_found_title",
        "agent.diag.not_found_body",
        "agent.diag.account_error",
        # sign-in (SignInPage)
        "signin.heading",
        "signin.tagline",
        "signin.pitch",
        "signin.privacy",
        "signin.link_expired_title",
        "signin.link_expired_body",
        "signin.signed_out",
        "signin.offline",
        "signin.email_hint",
        "signin.submit",
        "signin.sending",
        "signin.google",
        "signin.google_opening",
        "signin.send_failed_title",
        "signin.unavailable_title",
        # sign-in callback (CallbackPage)
        "callback.processing_heading",
        "callback.processing",
        "callback.unavailable_title",
        "callback.failed_heading",
        "callback.failed_title",
        "callback.back",
        "callback.expired",
        "callback.other_browser",
        "callback.denied",
        "callback.invalid",
        # account sign-out (AccountPage)
        "account.sign_out",
        "account.signing_out",
        "account.sign_out_all",
        "account.sign_out_all_confirm",
        "account.sign_out_everywhere",
        "account.signing_out_everywhere",
        "account.cancel",
        "account.sign_out_failed_title",
        "account.sign_out_failed",
        "account.settings_label",
        "account.signed_in_as",
        "account.name_too_long",
        # S03 routes and shell (AccountPage, SignInPage, TodayPage, AppShell)
        "account.display_name",
        "account.time_zone",
        "account.device_tz",
        "account.use_device_tz",
        "account.save_failed_title",
        "account.saving",
        "account.save",
        "account.saved",
        "account.offline_save",
        "account.heading",
        "account.loading",
        "account.load_failed_title",
        "common.error_fallback",
        "account.profile_heading",
        "account.no_email",
        "account.email_verified",
        "account.email_unverified",
        "account.admin_chip",
        "account.sign_out_heading",
        "signin.error_rate_limited",
        "signin.error_invalid_email",
        "signin.error_signup_disabled",
        "signin.error_unreachable",
        "signin.error_generic",
        "signin.email_invalid",
        "signin.google_failed",
        "signin.welcome",
        "common.reload",
        "signin.sent_heading",
        "signin.sent_body",
        "signin.resend_in",
        "signin.resend",
        "signin.different_email",
        "signin.or",
        "signin.email_label",
        "signin.email_placeholder",
        "signin.about_label",
        "today.heading",
        "today.loading",
        "today.load_failed_title",
        "today.days_left",
        "today.one_day_left",
        "today.exam_day",
        "today.exam_passed",
        "today.until_exam",
        "today.exam_date_passed",
        "today.greeting_morning",
        "today.greeting_afternoon",
        "today.greeting_evening",
        "today.greeting_morning_name",
        "today.greeting_afternoon_name",
        "today.greeting_evening_name",
        "today.date_line",
        "today.add_name",
        "today.exam_label",
        "today.exam_name",
        "today.exam_date",
        "today.no_exam_date",
        "today.tz_label",
        "today.change_tz_before",
        "today.change_tz_link",
        "today.change_tz_after",
        "exam.step1",
        "exam.step2ck",
        "exam.step3",
        "shell.nav_main",
        "shell.nav_tabs",
        "shell.home_label",
        "shell.offline",
        "shell.nav_failed_title",
        "shell.account_tab",
        "shell.account_label",
        # reader and content renderer (S06)
        # Qbank builder and tutor mode (S08)
        "practice.headline",
        "practice.headline_muted",
        "practice.past_blocks",
        "practice.group_mode",
        "practice.group_exam",
        "practice.group_systems",
        "practice.group_disciplines",
        "practice.group_status",
        "practice.group_difficulty",
        "practice.mode_tutor",
        "practice.mode_timed",
        "practice.mode_exam",
        "practice.mode_unavailable",
        "practice.status_all",
        "practice.status_unused",
        "practice.status_incorrect",
        "practice.status_flagged",
        "practice.status_correct",
        "practice.difficulty_all",
        "practice.difficulty_easy",
        "practice.difficulty_medium",
        "practice.difficulty_hard",
        "practice.your_block",
        "practice.fewer",
        "practice.more",
        "practice.count_label",
        "practice.start",
        "practice.building",
        "practice.all_systems",
        "practice.summary",
        "practice.matches",
        "practice.matches_one",
        "practice.empty_pool",
        "practice.weak_kicker",
        "practice.weak_body",
        "practice.weak_cta",
        "practice.weak_building",
        "practice.past_empty",
        "practice.past_progress",
        "practice.past_correct",
        "practice.past_continue",
        "practice.past_review",
        "practice.load_error_title",
        "practice.exit",
        "practice.progress",
        "practice.flag",
        "practice.flagged",
        "practice.labs",
        "practice.next",
        "practice.previous",
        "practice.submit",
        "practice.finish",
        "practice.correct",
        "practice.incorrect",
        "practice.your_answer",
        "practice.correct_answer",
        "practice.why_it_is",
        "practice.why_not",
        "practice.objective",
        "practice.lesson",
        "practice.report",
        "practice.report_what",
        "practice.ink",
        "practice.offline_saved",
        "practice.offline_question",
        "practice.question_error_title",
        "practice.labs_source",
        "practice.labs_official",
        "practice.block_done_title",
        "practice.block_done_body",
        "practice.back_to_builder",
        "practice.keys_hint",
        "practice.next_question",
        "practice.loading",
        "practice.loading_question",
        "practice.loading_labs",
        "practice.generic_error",
        "practice.retry",
        "practice.labs_col_test",
        "practice.labs_col_range",
        "practice.labs_col_si",
        "practice.new_tab",
        "practice.empty_exam",
        "practice.past_loading",
        "practice.past_error",
        "practice.options_label",
        "practice.result_incorrect",
        "practice.weak_block",
        "practice.explanation_label",
        "practice.question_gone_title",
        "practice.back_to_practice",
        # Search screen (S07)
        "search.title",
        "search.tab",
        "search.placeholder",
        "search.clear",
        "search.filters",
        "search.filter.all",
        "search.filter.chapters",
        "search.filter.notes",
        "search.filter.handwriting",
        "search.filter.questions",
        "search.filter.uploads",
        "search.filter.pathway",
        "search.filter.web",
        "search.group.chapters",
        "search.group.notes",
        "search.group.questions",
        "search.group.uploads",
        "search.group.pathway",
        "search.group.web",
        "search.kind.section",
        "search.kind.upload",
        "search.kind.question",
        "search.kind.note",
        "search.kind.ink",
        "search.kind.pathway",
        "search.kind.web",
        "search.intro_title",
        "search.intro_body",
        "search.loading",
        "search.count",
        "search.count_one",
        "search.empty_title",
        "search.empty_body",
        "search.error_title",
        "search.error_retry",
        "search.offline_title",
        "search.offline_body",
        "search.web_ask",
        "search.web_label",
        "search.web_loading",
        "search.web_empty",
        "search.web_unavailable",
        "search.web_limit",
        "search.web_opens",
        "search.meta.question",
        "search.meta.note_section",
        "search.meta.note_question",
        "search.meta.note_video",
        "search.meta.note_pdf",
        "search.meta.note_free",
        "search.meta.ink",
        "search.meta.guide",
        "search.error_body",
        "search.error_busy",
        "search.soon",
        "search.type_label",
        # courses and reader (S06)
        "content.callout_pearl",
        "content.callout_trap",
        "content.callout_bridge",
        "content.table",
        "content.table_high_yield",
        "content.unknown_node",
        "content.unknown_mark",
        "content.unknown_detail",
        "content.figure_fallback_alt",
        "content.figure_enlarge",
        "content.figure_close",
        "content.figure_missing",
        "content.source",
        "content.video_fallback_title",
        "content.video_play",
        "content.video_loads",
        "content.video_loads_with_length",
        "content.video_missing",
        "content.duration_s",
        "content.duration_min",
        "content.duration_min_s",
        "content.pdf_fallback_title",
        "content.pdf_open",
        "content.pdf_open_page",
        "content.pdf_missing",
        "content.question_label",
        "content.question_missing",
        "content.math_failed",
        "content.diagram_failed",
        "content.diagram_label",
        "content.diagram_as_text",
        "content.report_issue",
        "content.report_issue_prompt",
        "content.report_issue_question",
        "content.report_issue_hint",
        "content.report_issue_send",
        "content.report_issue_sending",
        "content.report_issue_sent",
        "content.report_issue_failed",
        "learn.title",
        "learn.headline_first",
        "learn.headline_mark",
        "learn.headline_second",
        "learn.loading",
        "learn.load_failed",
        "learn.error_generic",
        "learn.unreachable",
        "learn.not_available_body",
        "learn.rate_limited",
        "learn.retry",
        "learn.exam_group",
        "learn.saved_copy",
        "learn.cta_start",
        "learn.cta_continue",
        "learn.cta_next",
        "learn.systems",
        "learn.show_all_systems",
        "learn.tile_showing",
        "learn.all_chapters",
        "learn.empty_system",
        "learn.empty_exam",
        "learn.your_file",
        "learn.your_files",
        "learn.format_label",
        "learn.format_read",
        "learn.format_read_watch",
        "learn.status_label",
        "learn.status_done",
        "learn.status_progress",
        "learn.status_not_started",
        "learn.progress_label",
        "learn.progress_percent",
        "learn.progress_value",
        "learn.separator",
        "learn.count_chapter_one",
        "learn.count_chapter_other",
        "learn.count_section_one",
        "learn.count_section_other",
        "learn.count_figure_one",
        "learn.count_figure_other",
        "learn.count_video_one",
        "learn.count_video_other",
        "learn.count_question_one",
        "learn.count_question_other",
        "learn.count_card_one",
        "learn.count_card_other",
        "learn.count_minute_one",
        "learn.count_minute_other",
        "learn.count_system_question_one",
        "learn.count_system_question_other",
        "learn.reader_loading",
        "learn.reader_chapter",
        "learn.reader_not_found",
        "learn.reader_unavailable",
        "learn.reader_load_failed",
        "learn.reader_saved_copy",
        "learn.back_to_courses",
        "learn.section_missing",
        "learn.section_moved",
        "learn.chapter_empty",
        "learn.first_section",
        "learn.document_title",
        "learn.where_chapter",
        "learn.where_chapter_short",
        "learn.section_of",
        "learn.chapter_has",
        "learn.chip_read",
        "learn.no_video",
        "learn.chip_questions",
        "learn.chip_cards",
        "learn.contents",
        "learn.contents_reading",
        "learn.contents_read",
        "learn.contents_started",
        "learn.page_dots",
        "learn.section_label",
        "learn.section_meta",
        "learn.section_empty",
        "learn.sections_nav",
        "learn.position",
        "learn.prev_section",
        "learn.next_section",
        "learn.next_short",
        "learn.next_chapter",
        "learn.next_chapter_named",
        "learn.quoted",
        "learn.close",
        "learn.cancel",
        "search.unlinked",
        # exam view (S09)
        "exam.title_exam",
        "exam.title_timed",
        "exam.mock.mock1",
        "exam.mock.mock2",
        "exam.loading",
        "exam.loading_question",
        "exam.error_title",
        "exam.question_error_title",
        "exam.back_to_practice",
        "exam.leave",
        "exam.ready_title",
        "exam.rule_block",
        "exam.rule_blocks",
        "exam.rule_break",
        "exam.rule_pause_exam",
        "exam.rule_pause_timed",
        "exam.rule_no_going_back",
        "exam.rule_silent",
        "exam.rule_review",
        "exam.start",
        "exam.start_block",
        "exam.not_now",
        "exam.position",
        "exam.position_single",
        "exam.position_short",
        "exam.question_heading",
        "exam.question_region",
        "exam.time_left",
        "exam.time_left_minutes",
        "exam.time_left_seconds",
        "exam.low_time",
        "exam.break_left",
        "exam.pause",
        "exam.pause_off",
        "exam.paused",
        "exam.paused_title",
        "exam.paused_body",
        "exam.resume",
        "exam.end_block",
        "exam.end_title",
        "exam.end_title_last",
        "exam.end_body",
        "exam.end_body_last",
        "exam.end_unanswered",
        "exam.end_confirm",
        "exam.end_confirm_last",
        "exam.keep_going",
        "exam.break_label",
        "exam.break_title",
        "exam.break_time_left",
        "exam.break_auto",
        "exam.previous",
        "exam.next",
        "exam.flag",
        "exam.flagged",
        "exam.questions",
        "exam.tools",
        "exam.navigator",
        "exam.tile.question",
        "exam.tile.current",
        "exam.tile.answered",
        "exam.tile.unanswered",
        "exam.tile.flagged",
        "exam.options_legend",
        "exam.option_chosen",
        "exam.option_struck",
        "exam.strike_hint",
        "exam.highlight_hint",
        "exam.clear_highlights",
        "exam.will_sync",
        "exam.clock_stale",
        "exam.assistant_timed",
        "exam.silent_note",
        "exam.announce_5",
        "exam.announce_1",
        "exam.tool.flag",
        "exam.tool.unflag",
        "exam.tool.highlight",
        "exam.tool.strike",
        "exam.tool.labs",
        "exam.tool.calculator",
        "exam.tool.scratch",
        "exam.tool.view",
        "exam.scratch_note",
        "exam.scratch_surface",
        "exam.close",
        "exam.view.text_size",
        "exam.view.size_1",
        "exam.view.size_2",
        "exam.view.size_3",
        "exam.view.size_4",
        "exam.view.contrast",
        "exam.view.contrast_normal",
        "exam.view.contrast_high",
        "exam.calc.display",
        "exam.calc.error",
        "exam.calc.clear",
        "exam.calc.clear_entry",
        "exam.calc.sign",
        "exam.calc.divide",
        "exam.calc.multiply",
        "exam.calc.subtract",
        "exam.calc.add",
        "exam.calc.point",
        "exam.calc.equals",
        "exam.error.offline",
        "exam.error.pause_exam_mode",
        "exam.error.not_started",
        "exam.error.not_current",
        "exam.error.section_ended",
        "exam.error.paused",
        "exam.error.on_break",
        "exam.error.finished",
        "exam.error.no_such_section",
        "exam.error.not_timed",
        "exam.error.not_finished",
        # results + review (S09)
        "results.title",
        "results.loading",
        "results.score_sr",
        "results.kicker",
        "results.kicker_mock",
        "results.sentence",
        "results.sentence_up",
        "results.sentence_down",
        "results.sentence_same",
        "results.review_misses",
        "results.review_miss_one",
        "results.review_all",
        "results.make_cards",
        "results.make_cards_unavailable",
        "results.stats_label",
        "results.stat_correct",
        "results.stat_correct_note",
        "results.stat_time",
        "results.stat_seconds",
        "results.stat_time_note",
        "results.stat_time_median",
        "results.stat_time_none",
        "results.stat_changed",
        "results.stat_changed_note",
        "results.stat_flagged",
        "results.stat_flagged_note",
        "results.stat_omitted",
        "results.stat_omitted_note",
        "results.grid_title",
        "results.grid_legend",
        "results.grid_section",
        "results.q_label",
        "results.q_correct",
        "results.q_missed",
        "results.q_omitted",
        "results.q_flagged",
        "results.change.right_to_wrong",
        "results.change.wrong_to_right",
        "results.change.wrong_to_wrong",
        "results.topics_title",
        "results.topics_systems",
        "results.topics_disciplines",
        "results.topics_clinical",
        "results.topics_competencies",
        "results.topic_score",
        "results.topic_score_sr",
        "results.topics_empty",
        "results.weak",
        "results.percent",
        "results.trend_title",
        "results.trend_label",
        "results.trend_one",
        "results.trend_table_caption",
        "results.trend_col_date",
        "results.trend_col_mode",
        "results.trend_col_score",
        "results.trend_this",
        "results.weights_title",
        "results.weights_legend_band",
        "results.weights_legend_share",
        "results.weights_scale",
        "results.weights_official",
        "results.weights_no_official",
        "results.weights_share",
        "results.weights_below",
        "results.weights_within",
        "results.weights_above",
        "results.weights_accuracy",
        "results.weights_none",
        "results.weights_empty",
        "results.insights_kicker",
        "results.insights_none",
        "results.insight.weakest_topic",
        "results.insight.strongest_topic",
        "results.insight.pace_slow",
        "results.insight.pace_fast",
        "results.insight.changes_hurt",
        "results.insight.changes_helped",
        "results.insight.flags_right",
        "results.insight.underweighted",
        "results.insight.trend_up",
        "results.insight.trend_down",
        "results.not_finished_title",
        "results.not_finished_body",
        "results.back_to_exam",
        "results.not_timed",
        "results.error_title",
        "results.gone_title",
        "results.gone_body",
        "results.offline",
        "results.retry",
        "results.review_title",
        "results.review_loading",
        "results.review_filter_label",
        "results.filter_all",
        "results.filter_missed",
        "results.filter_flagged",
        "results.filter_changed",
        "results.filter_empty",
        "results.review_position",
        "results.review_outside",
        "results.review_heading",
        "results.review_back",
        "results.review_prev",
        "results.review_next",
        "results.review_done",
        "results.review_omitted",
        "results.review_unanswered",
        "results.review_seconds",
        "results.review_keys",
        "results.review_missing_title",
        "results.review_missing",
        "practice.sections_group",
        "practice.sections_hint",
        "practice.sections_hint_untimed",
        "practice.sections_break",
        "practice.sections_not_enough",
        "practice.sections_summary",
        "practice.mock_group",
        "practice.mock_meta",
        "practice.mock_building",
        "practice.mock_unavailable",
        "practice.mock_not_found",
        # Flashcards (S10)
        "cards.title",
        "cards.progress",
        "cards.progress_total",
        "cards.card_label",
        "cards.side_front",
        "cards.side_back",
        "cards.front_hint",
        "cards.show_answer",
        "cards.ratings_label",
        "cards.rating_again",
        "cards.rating_hard",
        "cards.rating_good",
        "cards.rating_easy",
        "cards.rating_with_interval",
        "cards.interval_minutes",
        "cards.interval_hours",
        "cards.interval_day",
        "cards.interval_days",
        "cards.interval_months",
        "cards.interval_years",
        "cards.keys_hint",
        "cards.edit",
        "cards.new_card",
        "cards.open_source",
        "cards.origin_user",
        "cards.origin_assistant",
        "cards.origin_anki_import",
        "cards.origin_seed",
        "cards.deck_chip",
        "cards.scratch",
        "cards.scratch_write",
        "cards.scratch_clear",
        "cards.scratch_label",
        "cards.stuck",
        "cards.hint",
        "cards.explain",
        "cards.loading",
        "cards.empty_title",
        "cards.empty_next",
        "cards.empty_none",
        "cards.done_title",
        "cards.done_body",
        "cards.done_body_one",
        "cards.more",
        "cards.error_title",
        "cards.retry",
        "cards.retrying",
        "cards.offline",
        "cards.offline_none",
        "cards.pending",
        "cards.pending_one",
        "cards.sync_now",
        "cards.syncing",
        "cards.rating_failed",
        "cards.dialog_new",
        "cards.dialog_edit",
        "cards.field_front",
        "cards.field_back",
        "cards.field_deck",
        "cards.field_hint",
        "cards.field_required",
        "cards.field_too_long",
        "cards.deck_default",
        "cards.save",
        "cards.saving",
        "cards.cancel",
        "cards.delete",
        "cards.delete_confirm",
        "cards.delete_yes",
        "cards.deleting",
        "cards.saved",
        "cards.save_as",
        "cards.save_as_done",
        "cards.due_link",
        "cards.due_link_none",
        "cards.due_link_loading",
        "cards.error.card_not_found",
        "cards.error.card_not_editable",
        "cards.error.card_question_not_attempted",
        "cards.error.card_question_not_found",
        "cards.error.card_section_not_found",
        "cards.error.review_out_of_order",
        "cards.error.review_id_reused",
        "cards.error.review_too_old",
        "cards.error.cards_unavailable",
        "cards.error.card_zone_unknown",
        "cards.error.invalid",
        "cards.error.generic",
        # ink persistence and Settings → Pencil & ink (S12b)
        "ink.save.saved",
        "ink.save.saving",
        "ink.save.local",
        "ink.save.error",
        "ink.save.full",
        "ink.save.retry",
        "ink.page.toggle",
        "ink.page.toggle_on",
        "ink.page.outdated",
        "ink.page.surface",
        "settings.nav_label",
        "settings.nav_account",
        "settings.nav_ink",
        "settings.on",
        "settings.off",
        "settings.ink_title",
        "settings.ink_group",
        "settings.ink_pen_only",
        "settings.ink_pen_only_note",
        "settings.ink_predict",
        "settings.ink_predict_note",
        "settings.offline",
        "settings.saving",
        "settings.saved_on_account",
        "settings.save_failed",
    }
)


class UiCopy(RootModel[dict[str, str]]):
    @field_validator("root")
    @classmethod
    def _keys(cls, v: dict[str, str]) -> dict[str, str]:
        problems = [k for k in v if not re.match(COPY_KEY, k) or len(k) > 80]
        problems += [k for k, s in v.items() if not s.strip() or len(s) > 500]
        if problems:
            raise ValueError(f"invalid ui_copy entries (key: dotted lowercase; value: 1-500 chars): {sorted(problems)}")
        # A bound on the config row, not on the product: every string the app shows is a key here (568 once
        # S12a's ink block landed), so the bound leaves room for the remaining segments.
        if len(v) > 2000:
            raise ValueError("at most 2000 ui_copy strings")
        if len(v) > 1500:
            raise ValueError("at most 1500 ui_copy strings")
        missing = sorted(REQUIRED_UI_COPY_KEYS - set(v))
        if missing:
            raise ValueError(f"ui_copy is missing keys the app shows: {', '.join(missing)}")
        return v


class SourcesPolicy(_Strict):
    """Publishing gates (HANDOFF §7.1, config/sources.yaml header)."""

    block_without_license: Literal[True] = True
    block_noncommercial_on_paid: bool = True
    block_share_alike_in_proprietary: bool = True
    block_non_ai_ingest_from_rag: Literal[True] = True
    youtube_metadata_max_age_days: int = Field(default=30, ge=1, le=30)
    original_source_id: str = Field(default="naadi-original-v1", min_length=1)


# ── lab_values ──


class LabRow(_Strict):
    analyte: str = Field(min_length=1, max_length=80)
    range: str = Field(min_length=1, max_length=80, description="Adult reference range, with the conventional unit")
    si: str | None = Field(default=None, max_length=80, description="The same range in SI units, when it differs")


class LabGroup(_Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    label: str = Field(min_length=1, max_length=60)
    rows: list[LabRow] = Field(min_length=1, max_length=60)


class LabValues(_Strict):
    """Naadi's own reference-range table for the question view's Lab values panel (original content, not a copy
    of the exam board's table: that one is © and is only linked to through its `sources` row)."""

    source_id: str = Field(min_length=1, max_length=80, description="`sources` row of this table (Naadi original)")
    official_source_id: str = Field(
        min_length=1, max_length=80, description="`sources` row whose URL is the official table to link out to"
    )
    groups: list[LabGroup] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def _unique_groups(self) -> LabValues:
        ids = [g.id for g in self.groups]
        if len(set(ids)) != len(ids):
            raise ValueError("lab group ids must be unique")
        return self


# ── fsrs (segment 10) ──


class FsrsConfig(_Strict):
    """Flashcard scheduling (FSRS-6). The API schedules with these and sends them with the due list, so the
    web's preview uses the same parameters. Defaults are ts-fsrs 6 / `fsrs` 6.3.3's (fuzz off, as ts-fsrs)."""

    weights: list[float] = Field(min_length=21, max_length=21, description="FSRS-6 model weights w0..w20")
    desired_retention: float = Field(ge=0.7, le=0.97, description="Target probability of recall when a card is due")
    learning_steps_s: list[int] = Field(max_length=10, description="New-card steps in seconds (default 1 min, 10 min)")
    relearning_steps_s: list[int] = Field(max_length=10, description="Steps after a lapse, in seconds (default 10 min)")
    maximum_interval: int = Field(ge=1, le=36500, description="Longest interval in days")
    enable_fuzz: bool = Field(description="Spread due dates by a few days (seeded, so previews still match)")

    @field_validator("learning_steps_s", "relearning_steps_s")
    @classmethod
    def _steps(cls, v: list[int]) -> list[int]:
        if any(s < 1 or s > 30 * 86400 for s in v):
            raise ValueError("each step is 1 second to 30 days")
        return v

    @field_validator("weights")
    @classmethod
    def _bounds(cls, v: list[float]) -> list[float]:
        from ..cards.scheduler import validate_params  # the fsrs package's own bounds check

        validate_params(v)
        return v


# ── registry ──


def _model_validator(model: Any) -> Callable[[object], Any]:
    adapter: TypeAdapter[Any] = TypeAdapter(model)

    def validate(value: object) -> Any:
        return adapter.dump_python(adapter.validate_python(value), mode="json", exclude_none=False)

    return validate


def _agent_validator(key: str) -> Callable[[object], Any]:
    def validate(value: object) -> Any:
        return llm_config.validate_section(key, value)

    return validate


@dataclass(frozen=True)
class KeySpec:
    key: str
    validate: Callable[[object], Any]
    public: bool
    source_file: str
    description: str
    json_schema: Callable[[], dict[str, Any]]


def _schema_of(model: Any) -> Callable[[], dict[str, Any]]:
    return lambda: TypeAdapter(model).json_schema()


def _agent_schema(key: str) -> Callable[[], dict[str, Any]]:
    return lambda: llm_config.SECTION_MODELS[key].model_json_schema()


KEY_SPECS: dict[str, KeySpec] = {
    spec.key: spec
    for spec in (
        *(
            KeySpec(k, _agent_validator(k), False, "models.yaml", f"Agent platform: {k}", _agent_schema(k))
            for k in llm_config.AGENT_CONFIG_KEYS
        ),
        KeySpec(
            "sources_policy",
            _model_validator(SourcesPolicy),
            False,
            "app_config.yaml",
            "Content license gates (config/sources.yaml header)",
            _schema_of(SourcesPolicy),
        ),
        KeySpec(
            "feature_flags",
            _model_validator(FeatureFlags),
            True,
            "app_config.yaml",
            "Feature flags",
            _schema_of(FeatureFlags),
        ),
        KeySpec("ui.nav", _model_validator(Nav), True, "app_config.yaml", "Top-level navigation", _schema_of(Nav)),
        KeySpec(
            "exam_rules",
            _model_validator(ExamRules),
            True,
            "app_config.yaml",
            "Block sizes and practice modes",
            _schema_of(ExamRules),
        ),
        KeySpec(
            "lab_values",
            _model_validator(LabValues),
            False,
            "app_config.yaml",
            "Lab values reference table (Qbank)",
            _schema_of(LabValues),
        ),
        KeySpec(
            "storage",
            _model_validator(StorageBuckets),
            False,
            "app_config.yaml",
            "Storage bucket names",
            _schema_of(StorageBuckets),
        ),
        KeySpec(
            "fsrs",
            _model_validator(FsrsConfig),
            False,
            "app_config.yaml",
            "Flashcard scheduling (FSRS-6)",
            _schema_of(FsrsConfig),
        ),
        KeySpec(
            "ui_copy", _model_validator(UiCopy), True, "app_config.yaml", "Editable UI strings", _schema_of(UiCopy)
        ),
    )
}
PUBLIC_KEYS = tuple(k for k, s in KEY_SPECS.items() if s.public)


class UnknownConfigKey(KeyError):
    pass


def validate_value(key: str, value: object) -> Any:
    """Validated, normalised JSON value for `key`; raises UnknownConfigKey or pydantic.ValidationError."""
    spec = KEY_SPECS.get(key)
    if spec is None:
        raise UnknownConfigKey(key)
    return spec.validate(value)


def cross_key_problems(values: Mapping[str, Any]) -> list[str]:
    """Checks that span keys: every nav item's feature flag exists."""
    problems: list[str] = []
    nav, flags = values.get("ui.nav"), values.get("feature_flags")
    if isinstance(nav, list) and isinstance(flags, Mapping):
        for item in nav:
            if isinstance(item, Mapping) and item.get("feature") not in flags:
                problems.append(f"ui.nav item {item.get('id')!r} uses unknown feature flag {item.get('feature')!r}")
    return problems
