---
description: "Custom instructions for CLI Tool development with Python"
applyTo: "**"
---

# CLI Tool Development Guidelines

## Programming Language: Python

**Python Best Practices:**
- Follow PEP 8 style guidelines strictly
- Use type hints for all function parameters and return values
- Prefer f-strings for string formatting over older methods
- Use descriptive variable and function names
- Implement proper error handling with specific exception types
- Use virtual environments for dependency management

## Code Style: Clean Code

**Clean Code Principles:**
- Write self-documenting code with meaningful names
- Keep functions small and focused on a single responsibility
- Avoid deep nesting and complex conditional statements
- Use consistent formatting and indentation
- Write code that tells a story and is easy to understand
- Refactor ruthlessly to eliminate code smells

## Project-Specific Guidelines

This project is a Pinterest Board Downloader that downloads all media from a board, and creates an archive from them for users to extract for later use, the media must be of the highest quality with the original filenames. Users must have the ability to download their own private boards via API authentication.

## AI Code Generation Preferences

When generating code, please:

- Generate complete, working code examples with proper imports
- Include inline comments for complex logic and business rules
- Follow the established patterns and conventions in this project
- Suggest improvements and alternative approaches when relevant
- Consider performance, security, and maintainability
- Include error handling and edge case considerations
- Generate appropriate unit tests when creating new functions
- Follow accessibility best practices for UI components
- Use semantic HTML and proper ARIA attributes when applicable
