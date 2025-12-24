#!/bin/bash
# Development Environment Setup Script
# Usage: ./scripts/dev_setup.sh [options]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_header() {
    echo -e "\n${BLUE}=== $1 ===${NC}\n"
}

print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
}

# Check Python
check_python() {
    print_header "Checking Python"
    if command -v python3 &> /dev/null; then
        PYTHON_VERSION=$(python3 --version)
        print_success "Python found: $PYTHON_VERSION"
    else
        print_error "Python 3 not found. Please install Python 3.9+"
        exit 1
    fi
}

# Setup virtual environment
setup_venv() {
    print_header "Setting up Virtual Environment"

    if [ -d "venv" ]; then
        print_warning "venv already exists"
    else
        python3 -m venv venv
        print_success "Created venv"
    fi

    # Activate venv
    source venv/bin/activate
    print_success "Activated venv"

    # Upgrade pip
    pip install --upgrade pip -q
    print_success "Upgraded pip"
}

# Install dependencies
install_deps() {
    print_header "Installing Dependencies"

    source venv/bin/activate
    pip install -r requirements.txt -q
    print_success "Installed production dependencies"

    # Dev dependencies
    pip install pytest pytest-asyncio pytest-mock pytest-cov watchdog -q
    print_success "Installed dev dependencies"
}

# Setup .env
setup_env() {
    print_header "Setting up Environment"

    if [ -f ".env" ]; then
        print_warning ".env already exists"
    else
        cp .env.example .env
        print_success "Created .env from .env.example"
        print_warning "Please edit .env and add your credentials"
    fi
}

# Create directories
setup_dirs() {
    print_header "Creating Directories"

    mkdir -p data logs sessions configs
    print_success "Created data/, logs/, sessions/, configs/"
}

# Setup configs
setup_configs() {
    print_header "Setting up Configs"

    if [ ! -f "configs/channels_config.json" ]; then
        cp configs/channels_config.example.json configs/channels_config.json
        print_success "Created channels_config.json"
    else
        print_warning "channels_config.json already exists"
    fi
}

# Docker services
start_docker() {
    print_header "Docker Services"

    if ! command -v docker &> /dev/null; then
        print_warning "Docker not found. Skip docker services."
        return
    fi

    echo "Available services:"
    echo "  1) ollama   - Local LLM server"
    echo "  2) weaviate - Vector database"
    echo "  3) both     - Start both"
    echo "  4) skip     - Don't start any"

    read -p "Start services [1/2/3/4]: " choice

    case $choice in
        1)
            docker-compose -f docker-compose.dev.yml up -d ollama
            print_success "Started Ollama"
            echo "Pull a model: docker exec dev_ollama ollama pull qwen2.5:3b"
            ;;
        2)
            docker-compose -f docker-compose.dev.yml up -d weaviate
            print_success "Started Weaviate on port 8081"
            ;;
        3)
            docker-compose -f docker-compose.dev.yml up -d ollama weaviate
            print_success "Started Ollama and Weaviate"
            echo "Pull a model: docker exec dev_ollama ollama pull qwen2.5:3b"
            ;;
        *)
            print_warning "Skipping docker services"
            ;;
    esac
}

# Run tests
run_tests() {
    print_header "Running Tests"

    source venv/bin/activate
    python -m pytest tests/ -v --tb=short
}

# Main
main() {
    echo -e "${BLUE}"
    echo "╔═══════════════════════════════════════════════════════╗"
    echo "║     Job Notification Bot - Dev Environment Setup      ║"
    echo "╚═══════════════════════════════════════════════════════╝"
    echo -e "${NC}"

    check_python
    setup_venv
    install_deps
    setup_env
    setup_dirs
    setup_configs

    # Optional docker services
    read -p "Setup Docker services? [y/N]: " docker_choice
    if [[ $docker_choice =~ ^[Yy]$ ]]; then
        start_docker
    fi

    # Run tests
    read -p "Run tests? [y/N]: " test_choice
    if [[ $test_choice =~ ^[Yy]$ ]]; then
        run_tests
    fi

    print_header "Setup Complete!"
    echo ""
    echo "Next steps:"
    echo "  1. Edit .env with your Telegram API credentials"
    echo "  2. Add at least one LLM API key (GROQ_API_KEY recommended)"
    echo "  3. Start the web interface:"
    echo "     source venv/bin/activate"
    echo "     python -m uvicorn web.app:app --reload --port 8080"
    echo ""
    echo "  4. Or start the full bot:"
    echo "     python main_multi.py"
    echo ""
    echo "For AI conversation testing without Telegram:"
    echo "     python tests/conversation_tester.py"
    echo ""
}

# Run
main "$@"
