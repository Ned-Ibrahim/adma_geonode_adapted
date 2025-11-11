# Static Documentation for GitHub Pages

This directory contains a static HTML version of the ADMA system documentation that can be hosted on GitHub Pages.

## Files

- `static_documentation.html` - Complete static HTML documentation
- `STATIC_DOCS_README.md` - This file with hosting instructions

## Hosting on GitHub Pages

### Option 1: Direct HTML File

1. **Upload to GitHub Repository:**
   ```bash
   # Copy the static documentation to your repository
   cp static_documentation.html /path/to/your/github/repo/index.html
   cd /path/to/your/github/repo
   git add index.html
   git commit -m "Add ADMA documentation"
   git push origin main
   ```

2. **Enable GitHub Pages:**
   - Go to your repository on GitHub
   - Navigate to Settings → Pages
   - Under "Source", select "Deploy from a branch"
   - Choose "main" branch and "/ (root)" folder
   - Click "Save"

3. **Access Documentation:**
   - Your documentation will be available at: `https://your-username.github.io/your-repo-name/`

### Option 2: Docs Folder

1. **Create docs folder:**
   ```bash
   mkdir docs
   cp static_documentation.html docs/index.html
   git add docs/
   git commit -m "Add ADMA documentation to docs folder"
   git push origin main
   ```

2. **Configure GitHub Pages:**
   - Go to Settings → Pages
   - Select "Deploy from a branch"
   - Choose "main" branch and "/docs" folder
   - Click "Save"

3. **Access Documentation:**
   - Available at: `https://your-username.github.io/your-repo-name/`

### Option 3: Separate Documentation Repository

1. **Create new repository:**
   ```bash
   # Create a new repository named 'adma-docs' on GitHub
   git clone https://github.com/your-username/adma-docs.git
   cd adma-docs
   cp /path/to/static_documentation.html index.html
   git add index.html
   git commit -m "Initial ADMA documentation"
   git push origin main
   ```

2. **Enable GitHub Pages:**
   - Repository Settings → Pages
   - Source: "Deploy from a branch"
   - Branch: "main", Folder: "/ (root)"

3. **Access Documentation:**
   - Available at: `https://your-username.github.io/adma-docs/`

## Customization

### Update GitHub Link

Edit the GitHub link in the static HTML file:

```html
<!-- Line ~95 in static_documentation.html -->
<div class="github-link">
    <a href="https://github.com/your-username/your-repo-name" target="_blank">
        <i class="fab fa-github me-1"></i>View on GitHub
    </a>
</div>
```

### Update Domain References

Replace placeholder domains in the documentation:

```html
<!-- Update these URLs throughout the file -->
https://your-domain.com/api/v1/auth/token/
https://your-domain.com/api/v1/files/upload/
```

### Custom Styling

The static documentation includes all CSS inline, so you can:

1. **Modify colors:** Change the `#d00000` color values to match your branding
2. **Update fonts:** Modify the font-family declarations
3. **Add custom sections:** Insert additional content sections as needed

## Features Included

The static documentation includes:

✅ **Complete Content:** All sections from the original Django template
✅ **Responsive Design:** Works on desktop, tablet, and mobile
✅ **Interactive Navigation:** Smooth scrolling sidebar navigation
✅ **Self-Contained:** All CSS and JavaScript inline (no external dependencies except CDN)
✅ **GitHub Integration:** Link to repository
✅ **Professional Styling:** Matches the original design
✅ **API Documentation:** Complete token-based API reference
✅ **Search Functionality:** Browser's built-in search (Ctrl+F)

## CDN Dependencies

The static documentation uses these CDN resources:

- **Bootstrap 5.3.0:** UI framework and responsive grid
- **Font Awesome 6.4.0:** Icons throughout the documentation
- **No jQuery:** Pure vanilla JavaScript for interactions

These are loaded from reliable CDNs and should work offline once cached.

## Maintenance

To update the documentation:

1. **Edit the static HTML file** directly, or
2. **Regenerate from Django template** if you make changes to the original
3. **Commit and push** changes to update GitHub Pages automatically

## Performance

The static documentation is optimized for:

- **Fast Loading:** Single HTML file with inline CSS/JS
- **SEO Friendly:** Proper heading structure and semantic HTML
- **Accessibility:** ARIA labels and keyboard navigation support
- **Mobile Optimized:** Responsive design with touch-friendly navigation

## Browser Support

Tested and working on:

- Chrome 90+
- Firefox 88+
- Safari 14+
- Edge 90+
- Mobile browsers (iOS Safari, Chrome Mobile)

## License

This documentation follows the same license as the main ADMA project.
