//! Native panel brightness, using the Linux backlight interface when available.
use std::{fs, io, path::{Path, PathBuf}};

pub struct Backlight {
    path: PathBuf,
    pub maximum: u32,
    pub current: u32,
}

impl Backlight {
    pub fn discover() -> Option<Self> {
        let mut panels: Vec<_> = fs::read_dir("/sys/class/backlight").ok()?
            .filter_map(Result::ok).map(|entry| entry.path()).collect();
        panels.sort();
        panels.into_iter().find_map(|path| Self::read(&path).ok())
    }

    fn read(path: &Path) -> io::Result<Self> {
        let number = |name| -> io::Result<u32> {
            fs::read_to_string(path.join(name))?.trim().parse()
                .map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))
        };
        let maximum = number("max_brightness")?;
        if maximum == 0 {
            return Err(io::Error::new(io::ErrorKind::InvalidData, "Invalid brightness range"));
        }
        Ok(Self { path: path.join("brightness"), maximum, current: number("brightness")?.min(maximum) })
    }

    pub fn minimum(&self) -> u32 {
        self.maximum.div_ceil(10)
    }

    pub fn set(&mut self, value: f64) -> io::Result<()> {
        if !value.is_finite() {
            return Err(io::Error::new(io::ErrorKind::InvalidInput, "Invalid brightness"));
        }
        let value = (value.round() as u32).clamp(self.minimum(), self.maximum);
        if value != self.current {
            fs::write(&self.path, format!("{value}\n"))?;
            self.current = value;
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_existing_level_and_clamps_writes_to_visible_range() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(dir.path().join("max_brightness"), "31\n").unwrap();
        fs::write(dir.path().join("brightness"), "15\n").unwrap();
        let mut panel = Backlight::read(dir.path()).unwrap();
        assert_eq!(panel.current, 15);
        panel.set(0.0).unwrap();
        assert_eq!(fs::read_to_string(&panel.path).unwrap(), "4\n");
        panel.set(100.0).unwrap();
        assert_eq!(fs::read_to_string(&panel.path).unwrap(), "31\n");
        assert!(panel.set(f64::NAN).is_err());
    }

    #[test]
    fn failed_write_keeps_last_applied_level() {
        let dir = tempfile::tempdir().unwrap();
        let mut panel = Backlight { path: dir.path().join("missing/brightness"), maximum: 31, current: 15 };
        assert!(panel.set(20.0).is_err());
        assert_eq!(panel.current, 15);
    }
}
