use std::{env, fs, path::Path};
fn main() {
    fn visit(dir: &Path, out: &mut String) {
        let mut entries: Vec<_> = fs::read_dir(dir)
            .unwrap()
            .map(|e| e.unwrap().path())
            .collect();
        entries.sort();
        for p in entries {
            if p.is_dir() {
                visit(&p, out);
            } else if p.extension().is_some_and(|x| x == "png") {
                let name = p.to_string_lossy().replace('\\', "/");
                let absolute = fs::canonicalize(&p).unwrap();
                out.push_str(&format!(
                    "({:?}, include_bytes!({:?})),\n",
                    name.trim_start_matches("src/"),
                    absolute
                ));
            }
        }
    }
    let mut out = String::from("pub static ASSETS: &[(&str, &[u8])] = &[\n");
    visit(Path::new("src/assets"), &mut out);
    out.push_str("];\n");
    fs::write(
        Path::new(&env::var("OUT_DIR").unwrap()).join("assets.rs"),
        out,
    )
    .unwrap();
    println!("cargo:rerun-if-changed=src/assets");
}
