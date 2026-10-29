// Native rendering of the original device-ui CSS layout. All sizes are logical pixels.
fn sans(size: f32, bold: bool) -> FontId {
    FontId::new(
        size,
        if bold {
            FontFamily::Name("Bold".into())
        } else {
            FontFamily::Proportional
        },
    )
}
fn rakkas(size: f32) -> FontId {
    FontId::new(size, FontFamily::Name("Rakkas".into()))
}
fn blend(color: Color32, background: Color32, opacity: f32) -> Color32 {
    Color32::from_rgb(
        (color.r() as f32 * opacity + background.r() as f32 * (1.0 - opacity)) as u8,
        (color.g() as f32 * opacity + background.g() as f32 * (1.0 - opacity)) as u8,
        (color.b() as f32 * opacity + background.b() as f32 * (1.0 - opacity)) as u8,
    )
}
fn label_at(ui: &Ui, point: Pos2, align: Align2, text: &str, font: FontId, color: Color32) {
    ui.painter().text(point, align, text, font, color);
}
fn text_width(ui: &Ui, text: &str, font: FontId) -> f32 {
    ui.painter()
        .layout_no_wrap(text.to_owned(), font, INK)
        .size()
        .x
}
fn css_button(
    ui: &mut Ui,
    rect: Rect,
    id: &str,
    text: &str,
    font: FontId,
    fill: Color32,
    ink: Color32,
    radius: u8,
    border: f32,
    enabled: bool,
    opacity: f32,
) -> Response {
    let response = ui.interact(
        rect,
        ui.id().with(id),
        if enabled {
            Sense::click()
        } else {
            Sense::hover()
        },
    );
    response.widget_info(|| WidgetInfo::labeled(WidgetType::Button, enabled, text));
    let amount = if enabled { 1.0 } else { opacity };
    let shift = if response.is_pointer_button_down_on() {
        1.0
    } else if response.hovered() && enabled {
        -1.0
    } else {
        0.0
    };
    let rect = rect.translate(vec2(0.0, shift));
    ui.painter()
        .rect_filled(rect, radius, blend(fill, CREAM, amount));
    if border > 0.0 {
        ui.painter().rect_stroke(
            rect,
            radius,
            Stroke::new(border, blend(INK, CREAM, amount)),
            StrokeKind::Inside,
        );
    }
    label_at(
        ui,
        rect.center(),
        Align2::CENTER_CENTER,
        text,
        font,
        blend(ink, CREAM, amount),
    );
    if enabled {
        response.on_hover_cursor(CursorIcon::PointingHand)
    } else {
        response
    }
}
fn html_range(
    ui: &mut Ui,
    rect: Rect,
    id: &str,
    value: &mut f64,
    min: f64,
    max: f64,
    step: f64,
    enabled: bool,
    color: Color32,
) -> Response {
    let mut response = ui.interact(
        rect,
        ui.id().with(id),
        if enabled {
            Sense::click_and_drag()
        } else {
            Sense::hover()
        },
    );
    // Include the release event: touch devices can deliver the final movement
    // and pointer-up together, after `dragged()` has become false.
    if enabled && (response.dragged() || response.drag_stopped() || response.clicked()) {
        response.request_focus();
        if let Some(p) = response.interact_pointer_pos() {
            let raw = min
                + ((p.x - rect.left() - 8.0) / (rect.width() - 16.0)).clamp(0.0, 1.0) as f64
                    * (max - min);
            let v = if step > 0.0 {
                ((raw - min) / step).round() * step + min
            } else {
                raw
            };
            if *value != v {
                *value = v.clamp(min, max);
                response.mark_changed();
            }
        }
    }
    if enabled && response.has_focus() {
        let old = *value;
        ui.input(|i| {
            if i.key_pressed(Key::ArrowLeft) || i.key_pressed(Key::ArrowDown) {
                *value -= step;
            }
            if i.key_pressed(Key::ArrowRight) || i.key_pressed(Key::ArrowUp) {
                *value += step;
            }
            if i.key_pressed(Key::Home) {
                *value = min;
            }
            if i.key_pressed(Key::End) {
                *value = max;
            }
        });
        *value = value.clamp(min, max);
        if old != *value {
            response.mark_changed();
        }
    }
    let fraction = ((*value - min) / (max - min).max(0.00001)).clamp(0.0, 1.0) as f32;
    let track = Rect::from_center_size(rect.center(), vec2(rect.width() - 4.0, 6.0));
    let thumb = pos2(
        rect.left() + 8.0 + (rect.width() - 16.0) * fraction,
        rect.center().y,
    );
    let alpha = if enabled { 1.0 } else { 0.35 };
    ui.painter()
        .rect_filled(track, 3, blend(Color32::from_gray(239), CREAM, alpha));
    ui.painter().rect_stroke(
        track,
        3,
        Stroke::new(1.0, blend(Color32::from_gray(178), CREAM, alpha)),
        StrokeKind::Inside,
    );
    let active = if enabled {
        color
    } else {
        Color32::from_gray(190)
    };
    ui.painter().rect_filled(
        Rect::from_min_max(track.min, pos2(thumb.x, track.bottom())),
        3,
        blend(active, CREAM, alpha),
    );
    ui.painter()
        .circle_filled(thumb, 8.0, blend(active, CREAM, alpha));
    response.widget_info(|| WidgetInfo::slider(enabled, *value, id));
    response
}
fn divider(ui: &mut Ui, margin: f32) {
    ui.add_space(margin);
    let (r, _) = ui.allocate_exact_size(vec2(ui.available_width(), 2.0), Sense::hover());
    ui.painter().rect_filled(r, 0, INK);
    ui.add_space(margin);
}
impl ChordSense {
    fn library_ui(&mut self, ctx: &Context) -> egui::ModalResponse<()> {
        let screen = ctx.screen_rect();
        let w = screen.width();
        let h = screen.height();
        let pad = if h <= 650.0 { 12.0 } else { 20.0 };
        let paper = Color32::from_rgb(245, 237, 224);
        egui::Modal::new(Id::new("library"))
            .backdrop_color(Color32::from_black_alpha(107))
            .frame(
                Frame::new()
                    .fill(paper)
                    .stroke(Stroke::new(1.0, Color32::from_black_alpha(46)))
                    .corner_radius(20)
                    .inner_margin(pad as i8),
            )
            .show(ctx, |ui| {
                let size = vec2(w * 0.95 - pad * 2.0 - 2.0, h * 0.94 - pad * 2.0 - 2.0);
                ui.set_min_size(size);
                ui.set_max_size(size);
                let r = Rect::from_min_size(ui.min_rect().min, size);
                let compact = h <= 650.0;
                let mut y = r.top();
                if !compact {
                    let brand = Rect::from_min_size(pos2(r.left(), y), vec2(97.0, 25.0));
                    ui.painter().rect_filled(brand, 7, INK);
                    label_at(
                        ui,
                        brand.center(),
                        Align2::CENTER_CENTER,
                        "ChordSense",
                        sans(13.0, true),
                        paper,
                    );
                    y += 37.0;
                }
                label_at(
                    ui,
                    pos2(r.left(), y + if compact { 14.0 } else { 18.0 }),
                    Align2::LEFT_CENTER,
                    "Library",
                    sans(if compact { 28.0 } else { 36.0 }, true),
                    INK,
                );
                let close = Rect::from_min_size(pos2(r.right() - 64.0, r.top()), vec2(64.0, 64.0));
                if css_button(
                    ui,
                    close,
                    "library-close",
                    "×",
                    sans(24.0, false),
                    blend(INK, paper, 0.07),
                    INK,
                    32,
                    0.0,
                    true,
                    1.0,
                )
                .clicked()
                {
                    self.library = false;
                }
                y += if compact { 28.0 } else { 36.0 };
                if !compact {
                    y += 8.0;
                    label_at(
                        ui,
                        pos2(r.left(), y + 9.0),
                        Align2::LEFT_CENTER,
                        "Choose a song to load.",
                        sans(16.0, false),
                        blend(INK, paper, 0.6),
                    );
                    y += 18.0;
                }
                y += if compact { 8.0 } else { 20.0 };
                for (i, title) in ["Uploaded Songs", "Recordings"].iter().enumerate() {
                    let active = self.recordings == (i == 1);
                    let tab = Rect::from_min_size(
                        pos2(r.left() + i as f32 * (r.width() + 8.0) / 2.0, y),
                        vec2((r.width() - 8.0) / 2.0, 64.0),
                    );
                    if css_button(
                        ui,
                        tab,
                        title,
                        title,
                        sans(20.0, true),
                        if active { INK } else { paper },
                        if active { paper } else { INK },
                        10,
                        1.5,
                        true,
                        1.0,
                    )
                    .clicked()
                    {
                        self.recordings = i == 1;
                        self.selected = None;
                    }
                }
                y += 64.0;
                if !compact {
                    y += 10.0;
                    label_at(
                        ui,
                        pos2(r.left(), y + 9.0),
                        Align2::LEFT_CENTER,
                        "Swipe to scroll. Tap a track, then choose Load or Rename.",
                        sans(16.0, true),
                        blend(INK, paper, 0.5),
                    );
                    y += 18.0 + 10.0;
                } else {
                    y += 8.0;
                }
                let actions_y = r.bottom() - 64.0;
                let line_y = actions_y - 17.0;
                let status_y = line_y - 18.0 - 8.0 - 10.0;
                let list = Rect::from_min_max(
                    pos2(r.left() + 4.0, y + 4.0),
                    pos2(r.right() - 4.0, status_y - 18.0),
                );
                let rows = if self.recordings {
                    self.takes.clone()
                } else {
                    self.files.clone()
                };
                ui.scope_builder(UiBuilder::new().max_rect(list), |ui| {
                    ui.set_clip_rect(list);
                    ui.spacing_mut().item_spacing.y = 10.0;
                    // Keep the scrollbar in its own lane beside the Delete buttons.
                    ui.spacing_mut().scroll.floating = false;
                    ui.spacing_mut().scroll.bar_inner_margin = 12.0;
                    ScrollArea::vertical()
                        .id_salt(("library_tracks", self.recordings))
                        // A new touch may arrive as a mouse move from the last
                        // touch location. That first delta is not a swipe.
                        .drag_to_scroll(!ui.input(|i| i.pointer.any_pressed()))
                        .auto_shrink([false, false])
                        .max_height(list.height().max(30.0))
                        .show(ui, |ui| {
                            if rows.is_empty() {
                                let (r, _) = ui
                                    .allocate_exact_size(vec2(list.width(), 90.0), Sense::hover());
                                label_at(
                                    ui,
                                    r.center(),
                                    Align2::CENTER_CENTER,
                                    if self.recordings {
                                        "No recordings yet."
                                    } else {
                                        "No songs uploaded yet."
                                    },
                                    sans(16.0, false),
                                    blend(INK, paper, 0.55),
                                );
                            }
                            for (i, row) in rows.iter().enumerate() {
                                let (r, _) = ui.allocate_exact_size(
                                    vec2(ui.available_width(), 80.0),
                                    Sense::hover(),
                                );
                                let song = Rect::from_min_max(
                                    r.min,
                                    pos2(
                                        r.right() - if self.recordings { 110.0 } else { 0.0 },
                                        r.bottom(),
                                    ),
                                );
                                let response =
                                    ui.interact(song, ui.id().with(("song", i)), Sense::click());
                                let selected = self.selected == Some(i);
                                let bg = if selected {
                                    INK
                                } else if response.hovered() {
                                    blend(Color32::WHITE, paper, 0.25)
                                } else {
                                    paper
                                };
                                ui.painter().rect_filled(song, 10, bg);
                                if selected || response.hovered() {
                                    ui.painter().rect_stroke(
                                        song,
                                        10,
                                        Stroke::new(
                                            1.0,
                                            if selected {
                                                INK
                                            } else {
                                                Color32::from_black_alpha(46)
                                            },
                                        ),
                                        StrokeKind::Inside,
                                    );
                                }
                                let title = row["title"]
                                    .as_str()
                                    .filter(|v| !v.is_empty())
                                    .unwrap_or(row["name"].as_str().unwrap_or("Track"));
                                let color = if selected { paper } else { INK };
                                let title_rect = Rect::from_min_max(
                                    song.min + vec2(20.0, 0.0),
                                    song.max - vec2(130.0, 0.0),
                                );
                                ui.painter().with_clip_rect(title_rect).text(
                                    pos2(title_rect.left(), song.center().y),
                                    Align2::LEFT_CENTER,
                                    title,
                                    sans(24.0, true),
                                    color,
                                );
                                label_at(
                                    ui,
                                    pos2(song.right() - 20.0, song.center().y),
                                    Align2::RIGHT_CENTER,
                                    &file_size(row["size"].as_u64().unwrap_or(0)),
                                    sans(17.0, false),
                                    blend(color, bg, 0.55),
                                );
                                response.widget_info(|| {
                                    WidgetInfo::labeled(WidgetType::Button, true, title)
                                });
                                if response.clicked() {
                                    self.selected = Some(i);
                                }
                                if response.double_clicked() {
                                    self.selected = Some(i);
                                    self.load_selected();
                                }
                                if self.recordings
                                    && css_button(
                                        ui,
                                        Rect::from_center_size(
                                            pos2(r.right() - 50.0, r.center().y),
                                            vec2(100.0, 64.0),
                                        ),
                                        &format!("delete-{i}"),
                                        "Delete",
                                        sans(20.0, true),
                                        paper,
                                        Color32::from_rgb(163, 40, 40),
                                        9,
                                        1.5,
                                        true,
                                        1.0,
                                    )
                                    .clicked()
                                {
                                    self.selected = Some(i);
                                    self.library_status.clear();
                                    self.delete = true;
                                }
                            }
                        });
                });
                label_at(
                    ui,
                    pos2(r.left(), status_y),
                    Align2::LEFT_CENTER,
                    &self.library_status,
                    sans(13.0, true),
                    INK,
                );
                ui.painter().line_segment(
                    [pos2(r.left(), line_y), pos2(r.right(), line_y)],
                    Stroke::new(1.0, Color32::from_black_alpha(36)),
                );
                let browse_w = text_width(ui, "Browse Device", sans(20.0, true)) + 43.0;
                if css_button(
                    ui,
                    Rect::from_min_size(pos2(r.left(), actions_y), vec2(browse_w, 64.0)),
                    "browse-device",
                    "Browse Device",
                    sans(20.0, true),
                    paper,
                    INK,
                    8,
                    1.5,
                    !self.picker_open,
                    0.35,
                )
                .clicked()
                {
                    self.picker_requested = true;
                }
                let labels = ["Rename", "Cancel", "Load Song"];
                let widths = labels.map(|label| text_width(ui, label, sans(20.0, true)) + 43.0);
                let mut x = r.right() - widths.iter().sum::<f32>() - 24.0;
                for (i, label) in labels.iter().enumerate() {
                    let enabled = i == 1 || self.selection().is_some();
                    if css_button(
                        ui,
                        Rect::from_min_size(pos2(x, actions_y), vec2(widths[i], 64.0)),
                        label,
                        label,
                        sans(20.0, true),
                        if i == 2 { INK } else { paper },
                        if i == 2 { paper } else { INK },
                        8,
                        1.5,
                        enabled,
                        0.35,
                    )
                    .clicked()
                    {
                        match i {
                            0 => {
                                if let Some(row) = self.selection() {
                                    self.rename_text = row["title"]
                                        .as_str()
                                        .filter(|s| !s.is_empty())
                                        .unwrap_or(row["name"].as_str().unwrap_or(""))
                                        .into();
                                    self.rename_target = Some(row);
                                    self.rename_kind = if self.recordings {
                                        "recordings"
                                    } else {
                                        "songs"
                                    }
                                    .into();
                                    self.rename_submitted = None;
                                    self.rename = true;
                                    self.keyboard = false;
                                    self.shift = false;
                                    self.rename_select_all = true;
                                    self.rename_cursor = None;
                                    self.library_status.clear();
                                }
                            }
                            1 => self.library = false,
                            _ => self.load_selected(),
                        }
                    }
                    x += widths[i] + 12.0;
                }
            })
    }
    fn image_at(&self, ui: &Ui, name: &str, rect: Rect, opacity: f32) {
        if let Some(t) = self.textures.get(&format!("assets/icons/{name}.png")) {
            ui.painter().image(
                t.id(),
                rect,
                Rect::from_min_max(Pos2::ZERO, pos2(1.0, 1.0)),
                Color32::from_white_alpha((255.0 * opacity) as u8),
            );
        }
    }
    fn header(&mut self, ui: &mut Ui, w: f32, h: f32) {
        let title_size = if self.focus {
            (w * 0.017).clamp(24.0, 36.0)
        } else if w <= 900.0 {
            (w * 0.05).clamp(26.0, 40.0)
        } else {
            (w * 0.03).clamp(30.0, 56.0)
        };
        let logo_size = if self.focus {
            (w * 0.017).clamp(22.0, 34.0)
        } else if w <= 900.0 {
            (w * 0.05).clamp(24.0, 38.0)
        } else {
            (w * 0.03).clamp(28.0, 54.0)
        };
        let header_h = if self.focus {
            logo_size * 1.5 + 8.0
        } else {
            (title_size * 2.84).max(80.0)
        };
        let (r, _) = ui.allocate_exact_size(vec2(ui.available_width(), header_h), Sense::hover());
        label_at(
            ui,
            pos2(r.left(), r.center().y),
            Align2::LEFT_CENTER,
            if self.record_mode {
                "Mode: Record"
            } else {
                "Mode: Play Along"
            },
            rakkas(title_size),
            INK,
        );
        let pad_x = if self.focus {
            12.0
        } else {
            (w * 0.018).clamp(14.0, 28.0)
        };
        let pad_y = if self.focus {
            4.0
        } else {
            (h * 0.007).clamp(4.0, 10.0)
        };
        let logo_w = text_width(ui, "ChordSense", rakkas(logo_size)) + pad_x * 2.0;
        let logo = Rect::from_center_size(
            pos2(r.right() - logo_w / 2.0, r.center().y),
            vec2(logo_w, logo_size * 1.5 + pad_y * 2.0),
        );
        ui.painter()
            .rect_filled(logo, if self.focus { 18 } else { 28 }, Color32::BLACK);
        label_at(
            ui,
            logo.center(),
            Align2::CENTER_CENTER,
            "ChordSense",
            rakkas(logo_size),
            Color32::WHITE,
        );
        let pair_size = (w * 0.01).clamp(13.0, 17.0);
        let pair_w = text_width(ui, "Pair uploads", sans(pair_size, true))
            + (w * 0.015).clamp(16.0, 28.0) * 2.0;
        let pair = Rect::from_center_size(
            pos2(logo.left() - 12.0 - pair_w / 2.0, r.center().y),
            vec2(pair_w, 44.0),
        );
        if css_button(
            ui,
            pair,
            "pair",
            "Pair uploads",
            sans(pair_size, true),
            blend(INK, CREAM, 0.07),
            INK,
            24,
            0.0,
            true,
            1.0,
        )
        .clicked()
        {
            self.pairing = true;
            self.pair_action("start");
        }
        if !self.focus {
            let switch = Rect::from_center_size(r.center() - vec2(0.0, 3.0), vec2(332.0, 80.0));
            ui.painter()
                .rect_stroke(switch, 40, Stroke::new(2.0, INK), StrokeKind::Inside);
            for (i, name) in ["Play Along", "Record"].iter().enumerate() {
                let active = self.record_mode == (i == 1);
                let rect = Rect::from_min_size(
                    switch.min + vec2(6.0 + i as f32 * 162.0, 6.0),
                    vec2(158.0, 68.0),
                );
                let rect = if active {
                    rect.expand2(vec2(1.58, 0.68))
                } else {
                    rect
                };
                if css_button(
                    ui,
                    rect,
                    name,
                    name,
                    sans(23.0, true),
                    if active { INK } else { CREAM },
                    if active { CREAM } else { INK },
                    40,
                    0.0,
                    self.record == Record::Idle,
                    0.35,
                )
                .clicked()
                    && !active
                {
                    self.stop();
                    self.record_mode = i == 1;
                }
            }
        }
        divider(
            ui,
            if self.focus {
                8.0
            } else if h <= 800.0 {
                10.0
            } else {
                (h * 0.02).clamp(12.0, 28.0)
            },
        );
    }
    fn capo_at(&mut self, ui: &mut Ui, r: Rect) {
        let mut x = r.left();
        let y = r.center().y;
        self.image_at(
            ui,
            "capo",
            Rect::from_center_size(pos2(x + 18.0, y), vec2(36.0, 36.0)),
            1.0,
        );
        x += 46.0;
        label_at(
            ui,
            pos2(x, y),
            Align2::LEFT_CENTER,
            "Capo",
            sans(25.0, true),
            INK,
        );
        x += 84.7;
        let b = Rect::from_min_size(pos2(x, y - 32.0), vec2(64.0, 64.0));
        if css_button(
            ui,
            b,
            "capo-down",
            "−",
            sans(32.0, false),
            Color32::BLACK,
            Color32::WHITE,
            12,
            0.0,
            true,
            1.0,
        )
        .clicked()
        {
            self.capo = (self.capo - 1).max(0);
        }
        x += 76.0;
        label_at(
            ui,
            pos2(x + 20.0, y),
            Align2::CENTER_CENTER,
            &self.capo.to_string(),
            sans(28.0, true),
            INK,
        );
        x += 52.0;
        if css_button(
            ui,
            Rect::from_min_size(pos2(x, y - 32.0), vec2(64.0, 64.0)),
            "capo-up",
            "+",
            sans(32.0, false),
            Color32::BLACK,
            Color32::WHITE,
            12,
            0.0,
            true,
            1.0,
        )
        .clicked()
        {
            self.capo = (self.capo + 1).min(11);
        }
        x += 76.0;
        if css_button(
            ui,
            Rect::from_min_size(pos2(x, y - 32.0), vec2(118.94, 64.0)),
            "capo-reset",
            "No Capo",
            sans(20.0, false),
            CREAM,
            INK,
            8,
            2.0,
            true,
            0.35,
        )
        .clicked()
        {
            self.capo = 0;
        }
        x += 130.94;
        let text = format!(
            "Try fret {}",
            self.recommended.map_or("—".into(), |c| c.to_string())
        );
        if css_button(
            ui,
            Rect::from_min_size(pos2(x, y - 32.0), vec2(128.16, 64.0)),
            "capo-rec",
            &text,
            sans(20.0, false),
            CREAM,
            INK,
            8,
            2.0,
            self.recommended.is_some(),
            0.3,
        )
        .clicked()
        {
            self.capo = self.recommended.unwrap_or(0);
        }
    }
    fn song_at(&self, ui: &Ui, r: Rect, w: f32, h: f32) {
        let icon_size = if self.focus {
            32.0
        } else {
            (w * 0.03).clamp(30.0, 48.0)
        };
        self.image_at(
            ui,
            "vinyl",
            Rect::from_center_size(
                pos2(r.left() + icon_size / 2.0, r.center().y),
                vec2(icon_size, icon_size),
            ),
            1.0,
        );
        let fs = if self.focus {
            17.0
        } else {
            (w * 0.014).clamp(15.0, 22.0)
        };
        let px = if self.focus {
            12.0
        } else {
            (w * 0.015).clamp(12.0, 22.0)
        };
        let py = if self.focus {
            6.0
        } else {
            (h * 0.01).clamp(7.0, 12.0)
        };
        let prefix = "Now Playing: ";
        let pw = text_width(ui, prefix, sans(fs, false));
        let width = (pw + text_width(ui, &self.name, sans(fs, true)) + px * 2.0)
            .min(r.width() - icon_size - 12.0);
        let pill = Rect::from_center_size(
            pos2(r.left() + icon_size + 12.0 + width / 2.0, r.center().y),
            vec2(width, fs * 1.18 + py * 2.0),
        );
        ui.painter().rect_filled(pill, 14, Color32::BLACK);
        let painter = ui.painter().with_clip_rect(pill.shrink2(vec2(px, 0.0)));
        painter.text(
            pos2(pill.left() + px, pill.center().y),
            Align2::LEFT_CENTER,
            prefix,
            sans(fs, false),
            Color32::WHITE,
        );
        painter.text(
            pos2(pill.left() + px + pw, pill.center().y),
            Align2::LEFT_CENTER,
            &self.name,
            sans(fs, true),
            Color32::WHITE,
        );
    }
    fn transport_at(&mut self, ui: &mut Ui, r: Rect, w: f32) {
        let icon = if self.focus {
            48.0
        } else {
            (w * 0.04).clamp(44.0, 64.0)
        };
        let image_size = if self.focus {
            44.0
        } else {
            (w * 0.035).clamp(38.0, 56.0)
        };
        let gap = if self.focus {
            (w * 0.008).clamp(8.0, 14.0)
        } else {
            16.0
        };
        let mut x = r.left();
        let y = r.center().y;
        for (i, name) in ["back", if self.playing { "pause" } else { "play-button" }]
            .iter()
            .enumerate()
        {
            let rect = Rect::from_center_size(pos2(x + icon / 2.0, y), vec2(icon, icon));
            let response = ui.interact(
                rect,
                ui.id().with(("transport", i)),
                if self.loaded {
                    Sense::click()
                } else {
                    Sense::hover()
                },
            );
            response.widget_info(|| {
                WidgetInfo::labeled(
                    WidgetType::Button,
                    self.loaded,
                    if i == 0 {
                        "Stop"
                    } else if self.playing {
                        "Pause"
                    } else {
                        "Play"
                    },
                )
            });
            self.image_at(
                ui,
                name,
                Rect::from_center_size(rect.center(), vec2(image_size, image_size)),
                if self.loaded { 1.0 } else { 0.35 },
            );
            if response.clicked() && self.loaded {
                if i == 0 {
                    self.stop()
                } else if self.playing {
                    self.pause()
                } else {
                    self.play()
                }
            }
            x += icon + gap;
        }
        let fs = (w * 0.011).clamp(14.0, 18.0);
        let speed_w = text_width(ui, "Speed", sans(fs, true))
            + (w * 0.09).clamp(100.0, 150.0)
            + fs * 4.3
            + 60.0;
        let focus_text = if self.focus {
            "Show Controls"
        } else {
            "Focus View"
        };
        let focus_w = text_width(ui, focus_text, sans(fs, true)) + 56.0;
        let timeline_w = if self.focus {
            (r.right() - x - speed_w - focus_w - gap * 2.0).max(100.0)
        } else {
            (w * 0.45).clamp(260.0, 700.0)
        };
        let mut pos = self.seek_preview.unwrap_or_else(|| self.current());
        let slider = html_range(
            ui,
            Rect::from_min_size(pos2(x, y - 36.0), vec2(timeline_w, 44.0)),
            "seek",
            &mut pos,
            0.0,
            self.duration.max(0.01),
            0.0,
            self.loaded,
            Color32::from_rgb(0, 117, 255),
        );
        if slider.dragged() {
            self.seek_preview = Some(pos);
        }
        if slider.drag_stopped() || (slider.changed() && !slider.dragged()) {
            self.seek_preview = None;
            self.seek(pos);
        }
        label_at(
            ui,
            pos2(x, y + 14.0),
            Align2::LEFT_CENTER,
            &format!("{} / {}", time(pos), time(self.duration)),
            sans((w * 0.013).clamp(14.0, 20.0), false),
            INK,
        );
        x += timeline_w + gap;
        let speed_rect = Rect::from_min_size(pos2(x, y - 24.0), vec2(speed_w, 48.0));
        ui.painter().rect_filled(speed_rect, 24, PAPER);
        ui.painter()
            .rect_stroke(speed_rect, 24, Stroke::new(2.0, INK), StrokeKind::Inside);
        label_at(
            ui,
            pos2(x + 14.0, y),
            Align2::LEFT_CENTER,
            "Speed",
            sans(fs, true),
            INK,
        );
        let sx = x + 14.0 + text_width(ui, "Speed", sans(fs, true)) + 12.0;
        let mut speed = self.speed_control;
        let slider = html_range(
            ui,
            Rect::from_center_size(
                pos2(sx + (w * 0.09).clamp(100.0, 150.0) / 2.0, y),
                vec2((w * 0.09).clamp(100.0, 150.0), 20.0),
            ),
            "speed",
            &mut speed,
            0.5,
            1.0,
            0.05,
            self.loaded,
            INK,
        );
        if slider.changed() {
            self.speed_control = speed;
        }
        if slider.drag_stopped() || (slider.changed() && !slider.dragged()) {
            self.fb_cutoff = now_ms();
            if let Some(r) = &mut self.rater {
                r.reset(Some(self.fb_session.clone()));
            }
            self.rating = None;
            self.playback("speed", json!({"speed":self.speed_control}));
        }
        label_at(
            ui,
            pos2(speed_rect.right() - 14.0, y),
            Align2::RIGHT_CENTER,
            &format!("{:.2}×", self.speed_control),
            sans(fs, true),
            INK,
        );
        x += speed_w + gap;
        if css_button(
            ui,
            Rect::from_min_size(pos2(x, y - 24.0), vec2(focus_w, 48.0)),
            "focus",
            focus_text,
            sans(fs, true),
            if self.focus { PAPER } else { INK },
            if self.focus { INK } else { CREAM },
            24,
            2.0,
            !self.timeline.is_empty(),
            0.35,
        )
        .clicked()
        {
            self.focus = !self.focus;
        }
    }
    fn play_ui(&mut self, ui: &mut Ui, w: f32, h: f32) {
        let width = ui.available_width();
        if !self.focus {
            let gap = (w * 0.01).clamp(8.0, 18.0);
            let tools_w = 209.375 + 137.391 + 253.0 + gap * 3.0 + 12.0 + 196.375;
            let same_row = width >= tools_w + gap + 593.813;
            let toolbar_h = if same_row { 72.0 } else { 72.0 + gap + 64.0 };
            let (r, _) = ui.allocate_exact_size(vec2(width, toolbar_h), Sense::hover());
            let mut x = r.left();
            if css_button(
                ui,
                Rect::from_min_size(pos2(x, r.top()), vec2(209.375, 72.0)),
                "browse",
                "Browse Tracks",
                sans(24.0, false),
                Color32::BLACK,
                Color32::WHITE,
                8,
                0.0,
                !self.busy.contains("loaded"),
                0.45,
            )
            .clicked()
            {
                self.library = true;
                self.refresh_library();
            }
            x += 209.375 + gap;
            let needs = self.loaded && self.timeline.is_empty() && !self.busy.contains("analyze");
            let ar = Rect::from_min_size(pos2(x, r.top()), vec2(137.391, 72.0));
            if needs {
                ui.painter().rect_stroke(
                    ar.expand(6.0),
                    11,
                    Stroke::new(3.0, Color32::from_rgb(36, 99, 60)),
                    StrokeKind::Inside,
                );
            }
            if css_button(
                ui,
                ar,
                "analyze",
                "Analyze",
                sans(24.0, false),
                if needs {
                    Color32::from_rgb(36, 99, 60)
                } else {
                    Color32::BLACK
                },
                Color32::WHITE,
                8,
                0.0,
                self.loaded && !self.busy.contains("analyze") && !self.playing,
                0.45,
            )
            .clicked()
            {
                self.analyze();
            }
            x += 137.391 + gap;
            if css_button(
                ui,
                Rect::from_min_size(pos2(x, r.top()), vec2(253.0, 72.0)),
                "feedback",
                if self.feedback {
                    "Live Feedback: On"
                } else {
                    "Live Feedback: Off"
                },
                sans(24.0, false),
                if self.feedback {
                    Color32::from_rgb(32, 93, 59)
                } else {
                    Color32::BLACK
                },
                Color32::WHITE,
                8,
                0.0,
                true,
                1.0,
            )
            .clicked()
            {
                self.feedback = !self.feedback;
                if !self.feedback {
                    self.stop_feedback()
                } else if self.playing {
                    self.start_feedback()
                }
            }
            x += 253.0 + gap + 12.0;
            label_at(
                ui,
                pos2(x, r.top() + 36.0),
                Align2::LEFT_CENTER,
                "Brightness",
                sans(16.0, false),
                INK,
            );
            x += 96.0;
            let (minimum, maximum) = self.backlight.as_ref()
                .map_or((0.0, 1.0), |panel| (panel.minimum() as f64, panel.maximum as f64));
            let brightness_slider = html_range(
                ui,
                Rect::from_min_size(pos2(x, r.top() + 26.0), vec2(98.4, 20.0)),
                "brightness",
                &mut self.brightness,
                minimum,
                maximum,
                1.0,
                self.backlight.is_some(),
                Color32::from_rgb(0, 117, 255),
            );
            if brightness_slider.changed() {
                if let Some(panel) = &mut self.backlight {
                    if let Err(error) = panel.set(self.brightness) {
                        self.status = format!("Could not change screen brightness: {error}");
                    }
                    self.brightness = panel.current as f64;
                }
            }
            brightness_slider.on_hover_text(if self.backlight.is_some() {
                format!("Screen brightness: {:.0}%", self.brightness / maximum * 100.0)
            } else {
                "Brightness control is unavailable for this display.".into()
            });
            self.capo_at(
                ui,
                Rect::from_min_size(
                    pos2(
                        r.right() - 593.813,
                        r.top() + if same_row { 4.0 } else { 72.0 + gap },
                    ),
                    vec2(593.813, 64.0),
                ),
            );
            ui.add_space(12.0);
            let (status, _) = ui.allocate_exact_size(vec2(width, 24.0), Sense::hover());
            label_at(
                ui,
                status.left_center(),
                Align2::LEFT_CENTER,
                &self.status,
                sans(16.0, false),
                INK,
            );
            ui.add_space(14.0);
            let song_h = (w * 0.03)
                .clamp(30.0, 48.0)
                .max((w * 0.014).clamp(15.0, 22.0) * 1.18 + 2.0 * (h * 0.01).clamp(7.0, 12.0));
            let (song, _) = ui.allocate_exact_size(vec2(width, song_h), Sense::hover());
            self.song_at(ui, song, w, h);
            ui.add_space(18.0);
            let (player, _) = ui.allocate_exact_size(
                vec2(width, (w * 0.04).clamp(44.0, 64.0).max(48.0)),
                Sense::hover(),
            );
            self.transport_at(ui, player, w);
            divider(
                ui,
                if h <= 800.0 {
                    10.0
                } else {
                    (h * 0.02).clamp(12.0, 28.0)
                },
            );
        } else {
            if w >= 1280.0 {
                let (r, _) = ui.allocate_exact_size(vec2(width, 48.0), Sense::hover());
                let song_w = (width - 20.0) / 3.0;
                self.song_at(ui, Rect::from_min_size(r.min, vec2(song_w, 48.0)), w, h);
                self.transport_at(
                    ui,
                    Rect::from_min_max(pos2(r.left() + song_w + 20.0, r.top()), r.max),
                    w,
                );
            } else {
                let (r, _) = ui.allocate_exact_size(vec2(width, 33.0), Sense::hover());
                self.song_at(ui, r, w, h);
                ui.add_space(4.0);
                let (r, _) = ui.allocate_exact_size(vec2(width, 48.0), Sense::hover());
                self.transport_at(ui, r, w);
            }
            ui.add_space(4.0);
            let (r, _) = ui.allocate_exact_size(vec2(width, 16.0), Sense::hover());
            label_at(
                ui,
                r.left_center(),
                Align2::LEFT_CENTER,
                &self.status,
                sans(13.0, false),
                INK,
            );
            ui.add_space(4.0);
        }
        // Controls take their space first; the chord field fills the remainder.
        let area_h = ui.available_height().max(0.0);
        let (rect, _) = ui.allocate_exact_size(vec2(width, area_h), Sense::hover());
        if self.timeline.is_empty() {
            let fs = (w * 0.032).clamp(28.0, 54.0);
            for (i, line) in ["Please Load a Song", "of Choice to Play", "Along to!"]
                .iter()
                .enumerate()
            {
                label_at(
                    ui,
                    rect.center() + vec2(0.0, (i as f32 - 1.0) * fs * 1.05),
                    Align2::CENTER_CENTER,
                    line,
                    rakkas(fs),
                    INK,
                );
            }
        } else {
            self.chord_field(ui, rect, w, h);
        }
    }
    fn chord_field(&self, ui: &Ui, r: Rect, w: f32, h: f32) {
        let p = chord_progression_at(&self.timeline, self.current());
        let names = [p.current, p.next, p.then].map(|c| {
            c.map(|c| chord_for_capo(&c.chord, self.capo))
                .unwrap_or("—".into())
        });
        let gap = (w * 0.015).clamp(10.0, 28.0);
        let first_w = (r.width() - gap * 2.0) * 1.6 / 3.4;
        let halo = if self.focus {
            (w * 0.44)
                .min(h - 230.0)
                .min(820.0)
                .min(first_w)
                .min(r.height())
        } else if h <= 850.0 {
            (w * 0.28).min(h * 0.44).clamp(280.0, 410.0)
        } else {
            (w * 0.27).min(h * 0.44).clamp(340.0, 500.0)
        }
        .min(r.height())
        .min(first_w);
        let center = if self.focus {
            pos2(r.left() + first_w / 2.0, r.center().y)
        } else {
            pos2(
                r.left() + r.width() * if w <= 1100.0 { 0.04 } else { 0.18 } + halo / 2.0,
                r.center().y,
            )
        };
        let painter = ui.painter();
        painter.circle_filled(center, halo / 2.0, Color32::from_rgb(205, 191, 168));
        painter.circle_filled(center, halo / 2.0 - (w * 0.009).clamp(9.0, 13.0), CREAM);
        let points = (0..=160)
            .map(|i| {
                let angle = std::f32::consts::TAU * (i as f32 / 160.0) * p.progress as f32;
                center + vec2(angle.cos(), angle.sin()) * (halo / 2.0 - 3.0)
            })
            .collect();
        painter.add(Shape::line(points, Stroke::new(halo * 0.026, INK)));
        let gate = center + vec2(halo / 2.0 - 2.0, 0.0);
        painter.circle_filled(gate, 12.0, CREAM);
        painter.circle_filled(gate, 8.0, INK);
        let diagram = (halo - 140.0).max(40.0);
        self.chord_card(
            ui,
            center,
            halo - 2.0 * (w * 0.009).clamp(9.0, 13.0) - 2.0 * (w * 0.01).clamp(10.0, 18.0),
            diagram,
            "CURRENT",
            &names[0],
            w,
            h,
            1.0,
        );
        let next_w = (w * 0.22).clamp(220.0, 420.0);
        let later_w = (w * 0.17).clamp(170.0, 300.0);
        let nx = if self.focus {
            r.left() + first_w + gap + (r.width() - gap * 2.0) / 3.4 / 2.0
        } else {
            r.left() + r.width() * if w <= 1100.0 { 0.40 } else { 0.5 } + next_w / 2.0
        };
        let lx = if self.focus {
            r.right() - (r.width() - gap * 2.0) * 0.8 / 3.4 / 2.0
        } else {
            nx + next_w / 2.0 + (w * 0.03).clamp(20.0, 80.0) + later_w / 2.0
        };
        self.chord_card(
            ui,
            pos2(nx, center.y),
            if self.focus {
                (halo - 96.0).max(100.0)
            } else {
                (h * 0.39).clamp(280.0, 460.0)
            }
            .min(r.height()),
            diagram * 0.9,
            "NEXT",
            &names[1],
            w,
            h,
            1.0,
        );
        self.chord_card(
            ui,
            pos2(lx, center.y),
            if self.focus {
                (halo - 126.0).max(90.0)
            } else {
                (h * 0.39).clamp(280.0, 460.0)
            }
            .min(r.height()),
            diagram * 0.8,
            "LATER",
            &names[2],
            w,
            h,
            if self.focus { 0.65 } else { 0.52 },
        );
    }
    fn chord_card(
        &self,
        ui: &Ui,
        center: Pos2,
        height: f32,
        diagram: f32,
        role: &str,
        label: &str,
        w: f32,
        h: f32,
        opacity: f32,
    ) {
        let fs = if self.focus {
            (w * 0.013).clamp(15.0, 24.0)
        } else {
            (w * 0.011).clamp(13.0, 18.0)
        };
        let role_h = fs * 1.2 + 2.0 * (h * 0.007).clamp(6.0, 10.0) + 4.0;
        let badge = Rect::from_center_size(
            center - vec2(0.0, height / 2.0 - role_h / 2.0),
            vec2(
                text_width(ui, role, sans(fs, true)) + 2.0 * (w * 0.012).clamp(13.0, 22.0) + 4.0,
                role_h,
            ),
        );
        let color = blend(INK, CREAM, opacity);
        ui.painter().rect_filled(
            badge,
            30,
            if role == "CURRENT" {
                INK
            } else {
                blend(Color32::WHITE, CREAM, 0.36 * opacity)
            },
        );
        ui.painter()
            .rect_stroke(badge, 30, Stroke::new(2.0, color), StrokeKind::Inside);
        label_at(
            ui,
            badge.center(),
            Align2::CENTER_CENTER,
            role,
            sans(fs, true),
            if role == "CURRENT" { CREAM } else { color },
        );
        let label_fs = if self.focus {
            if role == "CURRENT" {
                (w * 0.023).clamp(26.0, 42.0)
            } else if role == "NEXT" {
                (w * 0.02).clamp(23.0, 36.0)
            } else {
                (w * 0.018).clamp(21.0, 32.0)
            }
        } else if role == "CURRENT" {
            (w * 0.02).clamp(21.0, 34.0)
        } else if role == "NEXT" {
            (w * 0.017).clamp(18.0, 28.0)
        } else {
            (w * 0.014).clamp(16.0, 24.0)
        };
        let bottom = center.y + height / 2.0 - label_fs * 0.55;
        let diagram_center = pos2(
            center.x - (w * 0.015).clamp(8.0, 20.0),
            (badge.bottom() + bottom - label_fs * 0.55) / 2.0,
        );
        if let Some(texture) = chord_image_path(label).and_then(|p| self.textures.get(&p)) {
            let scale = diagram / texture.size_vec2().max_elem();
            ui.painter().image(
                texture.id(),
                Rect::from_center_size(diagram_center, texture.size_vec2() * scale),
                Rect::from_min_max(Pos2::ZERO, pos2(1.0, 1.0)),
                Color32::from_white_alpha((opacity * 255.0) as u8),
            );
        } else {
            label_at(
                ui,
                diagram_center,
                Align2::CENTER_CENTER,
                if label == "N" { "No chord" } else { label },
                rakkas(30.0),
                color,
            );
        }
        label_at(
            ui,
            pos2(center.x, bottom),
            Align2::CENTER_CENTER,
            label,
            rakkas(label_fs),
            color,
        );
    }
    fn record_ui(&mut self, ui: &mut Ui, w: f32, h: f32) {
        ui.add_space((h * 0.05).clamp(25.0, 70.0));
        let width = (w * 0.88).min(760.0);
        let extra = match self.record {
            Record::Countdown(_) => 150.0,
            Record::Recording(_) => 95.0,
            _ => 0.0,
        };
        let height = (h * 0.45).clamp(300.0, 500.0) + extra;
        let (row, _) = ui.allocate_exact_size(vec2(ui.available_width(), height), Sense::hover());
        let panel = Rect::from_center_size(row.center(), vec2(width, height));
        let bg = Color32::from_rgb(54, 53, 49);
        ui.painter()
            .rect_filled(panel, (w * 0.02).clamp(20.0, 32.0) as u8, bg);
        let indicator = (w * 0.04).clamp(44.0, 68.0);
        let content_h = indicator
            + (h * 0.02).clamp(16.0, 26.0)
            + 17.0
            + 8.0
            + (w * 0.04).clamp(34.0, 62.0) * 1.5
            + 10.0
            + (w * 0.013).clamp(15.0, 21.0) * 1.2
            + extra;
        let mut y = panel.center().y - content_h / 2.0 + indicator / 2.0;
        let pulse = if matches!(self.record, Record::Recording(_)) {
            0.675 + 0.325 * (now_ms() / 175.0).cos() as f32
        } else {
            1.0
        };
        ui.painter().circle_filled(
            pos2(panel.center().x, y),
            indicator / 2.0 + 8.0,
            blend(Color32::from_rgb(207, 52, 52), bg, 0.12),
        );
        ui.painter().circle_filled(
            pos2(panel.center().x, y),
            indicator / 2.0,
            blend(Color32::from_rgb(207, 52, 52), bg, pulse),
        );
        y += indicator / 2.0 + (h * 0.02).clamp(16.0, 26.0) + 8.5;
        label_at(
            ui,
            pos2(panel.center().x, y),
            Align2::CENTER_CENTER,
            "L I V E  I N P U T",
            sans((w * 0.009).clamp(11.0, 14.0), true),
            blend(CREAM, bg, 0.55),
        );
        y += 8.5 + 8.0;
        let (title, detail) = match self.record {
            Record::Idle => (
                "Ready to Record",
                "Press Start Recording when you're ready to play.",
            ),
            Record::Countdown(_) => ("Get Ready", "Recording starts after the countdown."),
            Record::Starting => ("Starting Recording", "Connecting to the microphone…"),
            Record::Recording(_) => ("Recording", "Play naturally. ChordSense is listening."),
            Record::Processing => ("Processing", "Detecting chords from your recording…"),
        };
        let ts = (w * 0.04).clamp(34.0, 62.0);
        label_at(
            ui,
            pos2(panel.center().x, y + ts * 0.75),
            Align2::CENTER_CENTER,
            title,
            rakkas(ts),
            CREAM,
        );
        y += ts * 1.5 + 10.0;
        label_at(
            ui,
            pos2(panel.center().x, y + 12.0),
            Align2::CENTER_CENTER,
            detail,
            sans((w * 0.013).clamp(15.0, 21.0), false),
            blend(CREAM, bg, 0.8),
        );
        match self.record {
            Record::Countdown(t) => label_at(
                ui,
                pos2(panel.center().x, y + 120.0),
                Align2::CENTER_CENTER,
                &(4 - t.elapsed().as_secs().min(3)).to_string(),
                rakkas((w * 0.12).clamp(90.0, 170.0)),
                CREAM,
            ),
            Record::Recording(t) => label_at(
                ui,
                pos2(panel.center().x, y + 75.0),
                Align2::CENTER_CENTER,
                &time(t.elapsed().as_secs_f64()),
                rakkas((w * 0.05).clamp(42.0, 78.0)),
                CREAM,
            ),
            _ => {}
        }
        ui.add_space((h * 0.03).clamp(22.0, 38.0));
        let bh = (h * 0.013).clamp(12.0, 17.0) * 2.0 + (w * 0.013).clamp(16.0, 21.0) * 1.2 + 4.0;
        let (buttons, _) = ui.allocate_exact_size(vec2(ui.available_width(), bh), Sense::hover());
        let bw = (w * 0.18).clamp(180.0, 250.0);
        let gap = (w * 0.015).clamp(12.0, 22.0);
        if css_button(
            ui,
            Rect::from_min_size(
                pos2(buttons.center().x - gap / 2.0 - bw, buttons.top()),
                vec2(bw, bh),
            ),
            "record-start",
            "Start Recording",
            sans((w * 0.013).clamp(16.0, 21.0), true),
            INK,
            Color32::WHITE,
            12,
            2.0,
            self.record == Record::Idle,
            0.3,
        )
        .clicked()
        {
            self.record = Record::Countdown(Instant::now());
            self.record_status = "Recording starts soon…".into();
        }
        if css_button(
            ui,
            Rect::from_min_size(
                pos2(buttons.center().x + gap / 2.0, buttons.top()),
                vec2(bw, bh),
            ),
            "record-stop",
            "Stop Recording",
            sans((w * 0.013).clamp(16.0, 21.0), true),
            CREAM,
            INK,
            12,
            2.0,
            matches!(self.record, Record::Countdown(_) | Record::Recording(_)),
            0.3,
        )
        .clicked()
        {
            if matches!(self.record, Record::Countdown(_)) {
                self.record = Record::Idle;
                self.record_status = "Recording cancelled.".into();
            } else {
                self.record = Record::Processing;
                self.request("record_end", 0, "POST", "/end_recording", json!({}));
            }
        }
        ui.add_space((h * 0.02).clamp(14.0, 24.0));
        let (r, _) = ui.allocate_exact_size(vec2(ui.available_width(), 24.0), Sense::hover());
        label_at(
            ui,
            r.center(),
            Align2::CENTER_CENTER,
            &self.record_status,
            sans((w * 0.012).clamp(14.0, 19.0), false),
            INK,
        );
    }
}
